import ast
import importlib.util
import os
import re
import sys
from typing import Dict, List, Set, Tuple

import pygraphviz as pgv

# Define built-in types to exclude
BUILTIN_TYPES = {
    'int', 'str', 'float', 'bool', 'list', 'dict', 'set', 'tuple', 'None',
    'Any', 'Optional', 'Union', 'Callable', 'Type', 'Iterable', 'Iterator',
    'Sequence', 'Mapping', 'ByteString', 'Bytes', 'Text', 'Complex', 'Number',
    'object'
}

def get_python_files(folder_path: str) -> List[str]:
    python_files = []
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    return python_files

def parse_classes(file_path: str) -> List[ast.ClassDef]:
    with open(file_path, 'r', encoding='utf-8') as file:
        file_content = file.read()
    tree = ast.parse(file_content, filename=file_path)
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    return classes

def extract_fields(cls_node: ast.ClassDef) -> Dict[str, str]:
    fields = {}
    # Check for Pydantic BaseModel inheritance
    is_pydantic = False
    for base in cls_node.bases:
        if isinstance(base, ast.Name) and base.id == 'BaseModel':
            is_pydantic = True
        elif isinstance(base, ast.Attribute) and base.attr == 'BaseModel':
            is_pydantic = True

    if is_pydantic:
        # Extract fields from class annotations
        for body_item in cls_node.body:
            if isinstance(body_item, ast.AnnAssign):
                field_name = body_item.target.id
                field_type = ast.unparse(body_item.annotation)
                fields[field_name] = field_type
    else:
        # Extract fields from __init__ method or class attributes
        for body_item in cls_node.body:
            if isinstance(body_item, ast.FunctionDef) and body_item.name == '__init__':
                for stmt in body_item.body:
                    if isinstance(stmt, ast.Assign):
                        for target in stmt.targets:
                            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
                                field_name = target.attr
                                if isinstance(stmt.value, ast.Name):
                                    field_type = stmt.value.id
                                else:
                                    field_type = type(stmt.value).__name__
                                fields[field_name] = field_type
            elif isinstance(body_item, ast.Assign):
                # Class attribute
                for target in body_item.targets:
                    if isinstance(target, ast.Name):
                        field_name = target.id
                        if isinstance(body_item.value, ast.Name):
                            field_type = body_item.value.id
                        else:
                            field_type = type(body_item.value).__name__
                        fields[field_name] = field_type
    return fields

def get_base_type(field_type: str) -> str:
    if not field_type:
        return None
    # Remove any module prefixes
    field_type = field_type.split('.')[-1]
    # Remove typing prefixes
    field_type = field_type.replace('typing.', '')
    # Handle common generic types
    match = re.match(r'^(?:List|Optional|Dict|Set|Tuple)\[(.+)\]$', field_type)
    if match:
        field_type = match.group(1)
    # Handle Union types
    elif field_type.startswith('Union['):
        types = field_type[6:-1].split(',')
        # Use the first type in the Union
        field_type = types[0].strip()
    # Remove any nested generics
    field_type = re.sub(r'\[.*\]', '', field_type)
    return field_type.strip()

def parse_imports(file_path: str) -> Dict[str, str]:
    import_map = {}
    with open(file_path, 'r', encoding='utf-8') as file:
        file_content = file.read()
    tree = ast.parse(file_content, filename=file_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                import_map[name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                full_name = f"{module}.{alias.name}" if module else alias.name
                import_map[name] = full_name
    return import_map

def find_module_file(module_name: str) -> str:
    try:
        spec = importlib.util.find_spec(module_name)
        if spec and spec.origin and spec.origin != 'built-in':
            return spec.origin
    except Exception as e:
        print(f"Could not find module {module_name}: {e}")
    return None

def build_class_map(folder_path: str) -> Tuple[Dict[str, Dict], Dict[str, str]]:
    class_map = {}
    file_class_map = {}
    visited_files = set()
    visited_classes = set()
    import_maps = {}

    def parse_file(file_path: str):
        if file_path in visited_files or not os.path.isfile(file_path):
            return
        visited_files.add(file_path)
        import_map = parse_imports(file_path)
        import_maps[file_path] = import_map
        classes = parse_classes(file_path)
        for cls in classes:
            class_name = cls.name
            if class_name in visited_classes:
                continue
            visited_classes.add(class_name)
            fields = extract_fields(cls)
            class_map[class_name] = {
                'fields': fields,
                'file': file_path,
                'local': True
            }
            file_class_map[class_name] = file_path

            # Parse field types recursively
            for field_type in fields.values():
                base_type = get_base_type(field_type)
                if base_type and base_type not in BUILTIN_TYPES and base_type not in class_map:
                    parse_class_by_name(base_type, import_map)

    def parse_class_by_name(class_name: str, import_map: Dict[str, str]):
        module_name = import_map.get(class_name)
        if not module_name:
            # Try to resolve module name from sys.modules
            module_name = sys.modules.get(class_name)
            if module_name:
                module_name = module_name.__name__
        if module_name:
            module_file = find_module_file(module_name)
            if module_file:
                parse_file(module_file)
            else:
                # Could not find module file; add as external
                if class_name not in class_map:
                    class_map[class_name] = {
                        'fields': {},
                        'file': None,
                        'local': False
                    }
        else:
            # Module name could not be resolved; add as external
            if class_name not in class_map:
                class_map[class_name] = {
                    'fields': {},
                    'file': None,
                    'local': False
                }

    def parse_folder(folder: str):
        python_files = get_python_files(folder)
        for file in python_files:
            parse_file(file)

    # Start parsing from the initial folder
    parse_folder(folder_path)
    return class_map, file_class_map

def visualize_schemas(class_map: Dict[str, Dict], file_class_map: Dict[str, str]):
    def sanitize_name(name: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    G = pgv.AGraph(directed=True, strict=False, rankdir='LR')
    file_colors = {}
    color_palette = ['#FF9999', '#99FF99', '#9999FF', '#FFCC99',
                     '#CC99FF', '#FF99CC', '#99CCFF', '#CCCCCC']
    color_index = 0

    # Assign colors to files
    files = set([info['file'] for info in class_map.values() if info['file']])
    for file in files:
        file_colors[file] = color_palette[color_index % len(color_palette)]
        color_index += 1

    # Create nodes
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info['fields']
        local = class_info.get('local', False)
        if local:
            file = class_info['file']
            color = file_colors.get(file, "#CCCCCC")
            # Create a label for the node
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
            # Create a placeholder node for imported classes
            G.add_node(sanitized_class_name, shape='box', style='dashed', label=class_name)
        print(f"Added node: {sanitized_class_name}")

    # Create edges
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info['fields']
        for field_name, field_type in fields.items():
            base_type = get_base_type(field_type)
            if base_type and base_type not in BUILTIN_TYPES:
                sanitized_base_type = sanitize_name(base_type)
                sanitized_field_name = sanitize_name(field_name)
                if sanitized_base_type in G.nodes():
                    # Use tailport and headport if source node is local and has ports
                    if class_info.get('local', False):
                        G.add_edge(
                            sanitized_class_name,
                            sanitized_base_type,
                            tailport=f"{sanitized_field_name}_type",
                            headport="class_header" if class_map[base_type].get('local', False) else "",
                            arrowhead='normal'
                        )
                    else:
                        # Source node is imported
                        G.add_edge(
                            sanitized_class_name,
                            sanitized_base_type,
                            arrowhead='normal'
                        )
                    print(f"Adding edge from '{sanitized_class_name}:{sanitized_field_name}_type' to '{sanitized_base_type}'")
                else:
                    print(f"Warning: Node '{sanitized_base_type}' does not exist in the graph.")
            else:
                print(f"Skipping built-in type '{base_type}' or unresolved type.")

    # Render the graph
    G.layout(prog='dot')
    G.draw('schemas.png')
    print("\nGraph Nodes:")
    for node in G.nodes():
        print(node)

    print("\nGraph Edges:")
    for edge in G.edges():
        print(edge)

def main(folder_path: str):
    class_map, file_class_map = build_class_map(folder_path)
    visualize_schemas(class_map, file_class_map)

if __name__ == '__main__':
    folder_path = './schemas/'  # Replace with your folder path
    main(folder_path)
