import os
import sys
import importlib
import inspect
import pygraphviz as pgv
import re
import dataclasses
from enum import Enum
from typing import get_args, get_origin, Union, List, Dict, Tuple, Set

# Import Pydantic if available
try:
    import pydantic
except ImportError:
    pydantic = None

# Define built-in types to exclude
BUILTIN_TYPES = set(sys.builtin_module_names) | {
    'int', 'str', 'float', 'bool', 'list', 'dict', 'set', 'tuple', 'NoneType',
    'Any', 'Optional', 'Union', 'Callable', 'Type', 'Iterable', 'Iterator',
    'Sequence', 'Mapping', 'ByteString', 'bytes', 'bytearray', 'memoryview',
    'Text', 'Complex', 'Number', 'object', 'type', 'frozenset', 'property',
    'staticmethod', 'classmethod', 'function'
}

def sanitize_name(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)

def parse_field_type(field_type):
    origin = get_origin(field_type)
    args = get_args(field_type)

    if origin is Union:
        type_names = []
        type_displays = []
        for arg in args:
            if arg is type(None):
                type_names.append('NoneType')
                type_displays.append('None')
            else:
                result = parse_field_type(arg)
                type_names.extend(result['types'])
                type_displays.append(result['display'])
        display = 'Union[' + ', '.join(type_displays) + ']'
        return {'display': display, 'types': type_names}
    elif origin in [list, List]:
        if args:
            result = parse_field_type(args[0])
            display = f'List[{result["display"]}]'
            return {'display': display, 'types': result['types']}
        else:
            return {'display': 'List', 'types': []}
    elif origin in [dict, Dict]:
        if args and len(args) == 2:
            key_result = parse_field_type(args[0])
            value_result = parse_field_type(args[1])
            display = f'Dict[{key_result["display"]}, {value_result["display"]}]'
            types = key_result['types'] + value_result['types']
            return {'display': display, 'types': types}
        else:
            return {'display': 'Dict', 'types': []}
    elif origin in [set, Set]:
        if args:
            result = parse_field_type(args[0])
            display = f'Set[{result["display"]}]'
            return {'display': display, 'types': result['types']}
        else:
            return {'display': 'Set', 'types': []}
    elif origin in [tuple, Tuple]:
        if args:
            types_list = []
            all_types = []
            for arg in args:
                result = parse_field_type(arg)
                types_list.append(result['display'])
                all_types.extend(result['types'])
            display = f"Tuple[{', '.join(types_list)}]"
            return {'display': display, 'types': all_types}
        else:
            return {'display': 'Tuple', 'types': []}
    else:
        if hasattr(field_type, '__name__'):
            type_name = field_type.__name__
            return {'display': type_name, 'types': [type_name]}
        else:
            return {'display': str(field_type), 'types': []}

def get_project_modules(project_root):
    local_modules = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        for filename in filenames:
            if filename.endswith('.py') and not filename.startswith('__'):
                filepath = os.path.join(dirpath, filename)
                # Compute module name
                rel_path = os.path.relpath(filepath, project_root)
                module_name = rel_path[:-3].replace(os.sep, '.')
                local_modules.append(module_name)
    return local_modules

def build_class_map(project_root):
    class_map = {}
    visited_classes = set()
    processing_queue = []

    from pathlib import Path

    def is_subpath(path, parent):
        path = Path(path).resolve()
        parent = Path(parent).resolve()
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def process_class(cls):
        class_name = cls.__name__
        if class_name in visited_classes or class_name in BUILTIN_TYPES:
            return
        visited_classes.add(class_name)
        fields = {}
        try:
            if pydantic and issubclass(cls, pydantic.BaseModel):
                # Pydantic model
                annotations = cls.__annotations__
                for field_name, field_type in annotations.items():
                    result = parse_field_type(field_type)
                    fields[field_name] = result
            elif dataclasses.is_dataclass(cls):
                # Dataclass
                for field in dataclasses.fields(cls):
                    field_name = field.name
                    field_type = field.type
                    result = parse_field_type(field_type)
                    fields[field_name] = result
            elif issubclass(cls, Enum):
                # Enum class
                # Extract enum members
                members = list(cls.__members__.keys())
                fields = {member_name: {'display': '', 'types': []} for member_name in members}
            else:
                # Regular class - extract public annotations
                annotations = getattr(cls, '__annotations__', {})
                for field_name, field_type in annotations.items():
                    result = parse_field_type(field_type)
                    fields[field_name] = result
        except Exception as e:
            print(f"Error processing class {class_name}: {e}")

        # Determine if class is local
        module = sys.modules.get(cls.__module__)
        if module:
            module_file = getattr(module, '__file__', None)
            if module_file:
                is_local = is_subpath(module_file, project_root)
            else:
                is_local = False
        else:
            is_local = False

        class_map[class_name] = {
            'fields': fields,
            'module': cls.__module__,
            'local': is_local,
            'is_enum': issubclass(cls, Enum)
        }

        # Enqueue field types for processing
        if not issubclass(cls, Enum):
            for field_info in fields.values():
                for base_type_name in field_info['types']:
                    if base_type_name and base_type_name not in visited_classes and base_type_name not in BUILTIN_TYPES:
                        processing_queue.append((base_type_name, cls.__module__))

    def resolve_class(name, current_module_name):
        # Try to resolve the class in the current module
        current_module = sys.modules.get(current_module_name)
        if current_module and hasattr(current_module, name):
            return getattr(current_module, name)

        # Try to find the class in already imported modules
        for mod_name, mod in list(sys.modules.items()):
            if hasattr(mod, name):
                return getattr(mod, name)

        # Try to import the module where the class might be
        try:
            # Assume the class is in a module with the same name
            mod = importlib.import_module(name)
            if hasattr(mod, name):
                return getattr(mod, name)
        except:
            pass

        # Try importing the module from the current module's package
        if current_module_name and '.' in current_module_name:
            parent_module_name = current_module_name.rsplit('.', 1)[0]
            try:
                mod = importlib.import_module(f"{parent_module_name}.{name}")
                if hasattr(mod, name):
                    return getattr(mod, name)
            except:
                pass

        # Try importing the class from the current module's package
        try:
            mod = importlib.import_module(parent_module_name)
            if hasattr(mod, name):
                return getattr(mod, name)
        except:
            pass

        return None

    # Add project root to sys.path
    sys.path.insert(0, project_root)

    # Process local modules
    local_modules = get_project_modules(project_root)
    for module_name in local_modules:
        try:
            module = importlib.import_module(module_name)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == module.__name__:
                    process_class(obj)
        except Exception as e:
            print(f"Failed to import module {module_name}: {e}")

    # Process classes in the queue
    while processing_queue:
        type_name, current_module_name = processing_queue.pop()
        if type_name in visited_classes or type_name in BUILTIN_TYPES:
            continue

        cls = resolve_class(type_name, current_module_name)
        if cls:
            process_class(cls)
        else:
            # If the class cannot be resolved, add it as a placeholder
            visited_classes.add(type_name)
            class_map[type_name] = {
                'fields': {},
                'module': None,
                'local': False,
                'is_enum': False
            }

    return class_map

def visualize_schemas(class_map: Dict[str, Dict]):
    G = pgv.AGraph(directed=True, strict=False, rankdir='LR')
    module_colors = {}
    color_palette = ['#FF9999', '#99FF99', '#9999FF', '#FFCC99',
                     '#CC99FF', '#FF99CC', '#99CCFF', '#CCCCCC']
    color_index = 0

    # Assign colors to modules
    modules = set(info['module'] for info in class_map.values() if info['module'])
    for module in sorted(modules):
        module_colors[module] = color_palette[color_index % len(color_palette)]
        color_index += 1

    # Create nodes
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info['fields']
        module = class_info.get('module')
        color = module_colors.get(module, "#CCCCCC")
        style = 'solid' if class_info.get('local') else 'dashed'

        if fields:
            if class_info.get('is_enum'):
                # Create a label for the enum node
                label = f"""<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0">
                <TR><TD BGCOLOR="{color}" COLSPAN="1"><B>{class_name} (Enum)</B></TD></TR>"""
                for member_name in fields.keys():
                    label += f"""<TR>
                    <TD>{member_name}</TD>
                    </TR>"""
                label += "</TABLE>>"
                G.add_node(sanitized_class_name, shape='plaintext', label=label, style=style)
            else:
                # Create a label for the node with fields
                label = f"""<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0">
                <TR><TD PORT="class_header" BGCOLOR="{color}" COLSPAN="2"><B>{class_name}</B></TD></TR>"""
                for field_name, field_info in fields.items():
                    sanitized_field_name = sanitize_name(field_name)
                    display_type = field_info['display']
                    label += f"""<TR>
                    <TD>{field_name}</TD>
                    <TD PORT="{sanitized_field_name}_type">{display_type}</TD>
                    </TR>"""
                label += "</TABLE>>"
                G.add_node(sanitized_class_name, shape='plaintext', label=label, style=style)
        else:
            # Create a placeholder node
            G.add_node(sanitized_class_name, shape='box', style=style, label=class_name)
        print(f"Added node: {sanitized_class_name}")

    # Create edges
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info['fields']
        for field_name, field_info in fields.items():
            for base_type_name in field_info['types']:
                if base_type_name and base_type_name not in BUILTIN_TYPES:
                    sanitized_base_type = sanitize_name(base_type_name)
                    sanitized_field_name = sanitize_name(field_name)
                    if sanitized_base_type in G.nodes():
                        # Use tailport and headport
                        G.add_edge(
                            sanitized_class_name,
                            sanitized_base_type,
                            tailport=f"{sanitized_field_name}_type",
                            headport="class_header",
                            arrowhead='normal'
                        )
                        print(f"Adding edge from '{sanitized_class_name}:{sanitized_field_name}_type' to '{sanitized_base_type}'")
                    else:
                        print(f"Warning: Node '{sanitized_base_type}' does not exist in the graph.")
                else:
                    print(f"Skipping built-in type '{base_type_name}' or unresolved type.")

    # Add legend node
    legend_label = """<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0">
    <TR><TD COLSPAN="2"><B>Legend</B></TD></TR>"""

    for module, color in module_colors.items():
        legend_label += f"""<TR>
        <TD BGCOLOR="{color}">&nbsp;&nbsp;&nbsp;&nbsp;</TD>
        <TD>{module}</TD>
        </TR>"""

    # Add node styles explanations
    legend_label += """<TR><TD COLSPAN="2"><B>Styles</B></TD></TR>"""
    legend_label += """<TR><TD>Solid Border</TD><TD>Local Class</TD></TR>"""
    legend_label += """<TR><TD>Dashed Border</TD><TD>Imported Class</TD></TR>"""

    legend_label += "</TABLE>>"

    G.add_node('Legend', shape='plaintext', label=legend_label)
    G.add_edge('Legend', 'Legend', style='invis', constraint='false')

    # Adjust graph attributes
    G.graph_attr.update(label='Schemas Diagram', labelloc='t')

    # Render the graph
    G.layout(prog='dot')
    G.draw('schemas.png')
    print("\nGraph Nodes:")
    for node in G.nodes():
        print(node)

    print("\nGraph Edges:")
    for edge in G.edges():
        print(edge)

def main():
    project_root = './schemas/'  # Replace with your project's root directory
    class_map = build_class_map(project_root)
    visualize_schemas(class_map)

if __name__ == '__main__':
    main()
