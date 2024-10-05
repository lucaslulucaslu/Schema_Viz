import importlib
import inspect
import sys
import pygraphviz as pgv
import re
import dataclasses
from typing import List, Dict
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

def get_base_type(field_type):
    if getattr(field_type, '__origin__', None) is not None:
        # Handle generic types like List[User]
        args = field_type.__args__
        if args:
            return get_base_type(args[0])
    elif isinstance(field_type, type):
        return field_type.__name__
    elif hasattr(field_type, '__name__'):
        return field_type.__name__
    return str(field_type)

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
                    base_type = get_base_type(field_type)
                    fields[field_name] = base_type
            elif dataclasses.is_dataclass(cls):
                # Dataclass
                for field in dataclasses.fields(cls):
                    field_name = field.name
                    field_type = field.type
                    base_type = get_base_type(field_type)
                    fields[field_name] = base_type
            else:
                # Regular class - extract public attributes
                attributes = [attr for attr in cls.__dict__ if not callable(getattr(cls, attr)) and not attr.startswith('_')]
                for attr in attributes:
                    try:
                        value = getattr(cls, attr)
                        base_type = type(value).__name__
                        fields[attr] = base_type
                    except Exception:
                        pass
        except Exception as e:
            print(f"Error processing class {class_name}: {e}")

        class_map[class_name] = {
            'fields': fields,
            'module': cls.__module__,
            'local': cls.__module__ in module_names
        }

        # Recursively process field types
        for base_type_name in fields.values():
            if base_type_name not in visited_classes and base_type_name not in BUILTIN_TYPES:
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
                            'local': False
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

        if fields:
            # Create a label for the node with fields
            label = f"""<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0">
            <TR><TD PORT="class_header" BGCOLOR="{color}" COLSPAN="2"><B>{class_name}</B></TD></TR>"""
            for field_name, field_type in fields.items():
                sanitized_field_name = sanitize_name(field_name)
                label += f"""<TR>
                <TD>{field_name}</TD>
                <TD PORT="{sanitized_field_name}_type">{field_type}</TD>
                </TR>"""
            label += "</TABLE>>"
            G.add_node(sanitized_class_name, shape='plaintext', label=label)
        else:
            # Create a placeholder node
            G.add_node(sanitized_class_name, shape='box', style='dashed', label=class_name)
        print(f"Added node: {sanitized_class_name}")

    # Create edges
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info['fields']
        for field_name, base_type_name in fields.items():
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
        'schemas.comment',  # Replace with your local module names
    ]

    class_map = build_class_map(module_names)
    visualize_schemas(class_map)

if __name__ == '__main__':
    main()
