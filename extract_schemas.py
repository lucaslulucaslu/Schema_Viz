import os
import ast
import pygraphviz as pgv
import re


def sanitize_name(name):
    # Replace any characters that are not letters, numbers, or underscores with underscores
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def get_python_files(folder_path):
    python_files = []
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.endswith(".py"):
                python_files.append(os.path.join(root, file))
    return python_files


def parse_classes(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        file_content = file.read()
    tree = ast.parse(file_content, filename=file_path)
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    return classes


def extract_fields(cls_node):
    fields = {}
    # Check for Pydantic BaseModel inheritance
    is_pydantic = False
    for base in cls_node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            is_pydantic = True
        elif isinstance(base, ast.Attribute) and base.attr == "BaseModel":
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
            if isinstance(body_item, ast.FunctionDef) and body_item.name == "__init__":
                for stmt in body_item.body:
                    if isinstance(stmt, ast.Assign):
                        for target in stmt.targets:
                            if (
                                isinstance(target, ast.Attribute)
                                and isinstance(target.value, ast.Name)
                                and target.value.id == "self"
                            ):
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


def get_base_type(field_type):
    # Remove any module prefixes
    field_type = field_type.split(".")[-1]
    # Remove typing prefixes (e.g., 'typing.List')
    field_type = field_type.replace("typing.", "")
    # Handle common generic types
    match = re.match(r"^(?:List|Optional|Dict|Set|Tuple)\[(.+)\]$", field_type)
    if match:
        field_type = match.group(1)
    # Handle Union types
    elif field_type.startswith("Union["):
        types = field_type[6:-1].split(",")
        # Use the first type in the Union for this example
        field_type = types[0].strip()
    # Remove any nested generics
    field_type = re.sub(r"\[.*\]", "", field_type)
    return field_type.strip()


def build_class_map(folder_path):
    python_files = get_python_files(folder_path)
    class_map = {}
    file_class_map = {}  # Map class names to their source files
    for file in python_files:
        classes = parse_classes(file)
        for cls in classes:
            class_name = cls.name
            fields = extract_fields(cls)
            class_map[class_name] = {"fields": fields, "file": file}
            file_class_map[class_name] = file
    return class_map, file_class_map


def visualize_schemas(class_map, file_class_map):
    G = pgv.AGraph(directed=True, strict=False, rankdir="LR")
    file_colors = {}
    color_palette = [
        "#FF9999",
        "#99FF99",
        "#9999FF",
        "#FFCC99",
        "#CC99FF",
        "#FF99CC",
        "#99CCFF",
        "#CCCCCC",
    ]
    color_index = 0

    # Assign colors to files
    for file in set(file_class_map.values()):
        file_colors[file] = color_palette[color_index % len(color_palette)]
        color_index += 1

    # Create nodes
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info["fields"]
        file = class_info["file"]
        color = file_colors[file]

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

        G.add_node(sanitized_class_name, shape="plaintext", label=label)
        print(f"Added node: {sanitized_class_name}")

    # Create edges
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info["fields"]
        for field_name, field_type in fields.items():
            base_type = get_base_type(field_type)
            sanitized_base_type = sanitize_name(base_type)
            sanitized_field_name = sanitize_name(field_name)
            print(
                f"Processing field '{field_name}' of class '{class_name}' with type '{field_type}' (base type: '{base_type}')"
            )

            if base_type in class_map:
                if G.has_node(sanitized_class_name) and G.has_node(sanitized_base_type):
                    G.add_edge(
                        sanitized_class_name,
                        sanitized_base_type,
                        tailport=f"{sanitized_field_name}_type",
                        headport="class_header",
                        arrowhead="normal",
                    )
                    print(
                        f"Adding edge from '{sanitized_class_name}:{sanitized_field_name}_type' to '{sanitized_base_type}:class_header'"
                    )
                else:
                    print(
                        f"Warning: Node '{sanitized_class_name}' or '{sanitized_base_type}' does not exist in the graph."
                    )
            else:
                print(f"Base type '{base_type}' not found in class map.")

    # Render the graph
    G.layout(prog="dot")
    G.write("schemas.dot")
    G.draw("schemas.png")
    print("\nGraph Nodes:")
    for node in G.nodes():
        print(node)

    print("\nGraph Edges:")
    for edge in G.edges():
        print(edge)


def main(folder_path):
    class_map, file_class_map = build_class_map(folder_path)
    visualize_schemas(class_map, file_class_map)


if __name__ == "__main__":
    folder_path = "./schemas/"  # Replace with your folder path
    main(folder_path)
