import importlib
import inspect
import sys
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
    'int', 'str', 'float', 'bool', 'list', 'dict', 'set', 'tuple', 'None',
    'Any', 'Optional', 'Union', 'Callable', 'Type', 'Iterable', 'Iterator',
    'Sequence', 'Mapping', 'ByteString', 'bytes', 'bytearray', 'memoryview',
    'Text', 'Complex', 'Number', 'object'
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
                type_names.append('None')
                type_displays.append('None')
            else:
                result = parse_field_type(arg)
                type_names.extend(result['types'])
                type_displays.append(result['display'])
        display = 'Union[' + ', '.join(type_displays) + ']'
        return {'display': display, 'types': type_names}
    elif origin is list or origin is List:
        if args:
            result = parse_field_type(args[0])
            display = f'List[{result["display"]}]'
            return {'display': display, 'types': result['types']}
        else:
            return {'display': 'List', 'types': []}
    elif origin is dict or origin is Dict:
        if args and len(args) == 2:
            key_result = parse_field_type(args[0])
            value_result = parse_field_type(args[1])
            display = f'Dict[{key_result["display"]}, {value_result["display"]}]'
            types = key_result['types'] + value_result['types']
            return {'display': display, 'types': types}
        else:
            return {'display': 'Dict', 'types': []}
    elif origin is set or origin is Set:
        if args:
            result = parse_field_type(args[0])
            display = f'Set[{result["display"]}]'
            return {'display': display, 'types': result['types']}
        else:
            return {'display': 'Set', 'types': []}
    elif origin is tuple or origin is Tuple:
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

def build_class_map(module_names: List[str]) -> Dict[str, Dict]:
    class_map = {}
    visited_classes = set()
    
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
                # Regular class - extract public attributes
                attributes = [attr for attr in cls.__dict__ if not callable(getattr(cls, attr)) and not attr.startswith('_')]
                for attr in attributes:
                    try:
                        value = getattr(cls, attr)
                        base_type = type(value)
                        result = parse_field_type(base_type)
                        fields[attr] = result
                    except Exception:
                        pass
        except Exception as e:
            print(f"Error processing class {class_name}: {e}")

        class_map[class_name] = {
            'fields': fields,
            'module': cls.__module__,
            'local': cls.__module__ in module_names,
            'is_enum': issubclass(cls, Enum)
        }

        # Enums don't have field types to process further
        if not issubclass(cls, Enum):
            # Recursively process field types
            for field_info in fields.values():
                for base_type_name in field_info['types']:
                    if base_type_name and base_type_name not in visited_classes and base_type_name not in BUILTIN_TYPES:
                        try:
                            base_cls = None
                            if base_type_name in sys.modules:
                                base_cls = sys.modules[base_type_name]
                            else:
                                base_cls = getattr(sys.modules.get(cls.__module__), base_type_name, None)
                                if base_cls is None:
                                    base_cls = getattr(importlib.import_module(base_type_name), base_type_name)
                            if base_cls:
                                process_class(base_cls)
                        except Exception as e:
                            print(f"Error importing class {base_type_name}: {e}")
                            if base_type_name not in class_map:
                                class_map[base_type_name] = {
                                    'fields': {},
                                    'module': None,
                                    'local': False,
                                    'is_enum': False
                                }

    # Import the specified modules and process their classes
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == module.__name__:
                    process_class(obj)
        except Exception as e:
            print(f"Failed to import module {module_name}: {e}")
    
    return class_map

def visualize_schemas(class_map: Dict[str, Dict]):
    G = pgv.AGraph(directed=True, strict=False, rankdir='LR')
    module_colors = {}
    color_palette = ['#FF9999', '#99FF99', '#9999FF', '#FFCC99',
                     '#CC99FF', '#FF99CC', '#99CCFF', '#CCCCCC']
    color_index = 0

    # Assign colors to modules
    modules = set(info['module'] for info in class_map.values() if info['module'])
    for module in modules:
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
    # Specify the modules you want to include
    module_names = [
        'schemas.comment',  # Replace with third-party modules you want to include
    ]

    class_map = build_class_map(module_names)
    visualize_schemas(class_map)

if __name__ == '__main__':
    main()
