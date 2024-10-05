"""Visualize schemas."""

import dataclasses
import importlib
import inspect
import re
import sys
from enum import Enum
from typing import Any, Dict, List, Union, get_args, get_origin

import pydantic
import pygraphviz as pgv  # type: ignore
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

# Define built-in types to exclude
BUILTIN_TYPES = set(sys.builtin_module_names) | {
    "int",
    "str",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "NoneType",
    "Any",
    "Optional",
    "Union",
    "Callable",
    "Type",
    "Iterable",
    "Iterator",
    "Sequence",
    "Mapping",
    "ByteString",
    "bytes",
    "bytearray",
    "memoryview",
    "Text",
    "Complex",
    "Number",
    "object",
    "type",
    "frozenset",
    "property",
    "staticmethod",
    "classmethod",
    "function",
}


def sanitize_name(name: str) -> str:
    """Sanitize class and field names to be Graphviz-friendly."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def parse_field_type(field_type: Any) -> Dict:
    """
    Parse the field type to handle Optional and Union types.

    Returns a dictionary with:
    - 'display': The string representation of the type for display.
    - 'types': A list of base type names for creating edges.
    """
    origin = get_origin(field_type)
    args = get_args(field_type)

    if origin is Union:
        type_names = []
        type_displays = []
        for arg in args:
            if arg is type(None):
                type_names.append("NoneType")
                type_displays.append("None")
            else:
                result = parse_field_type(arg)
                type_names.extend(result["types"])
                type_displays.append(result["display"])
        if len(args) == 2 and type(None) in args:
            # It's an Optional
            non_none_types = [t for t in args if t is not type(None)]
            result = parse_field_type(non_none_types[0])
            display = f"Optional[{result['display']}]"
        else:
            display = "Union[" + ", ".join(type_displays) + "]"
        return {"display": display, "types": type_names}
    elif origin in [list, List]:
        if args:
            result = parse_field_type(args[0])
            display = f'List[{result["display"]}]'
            return {"display": display, "types": result["types"]}
        else:
            return {"display": "List", "types": []}
    elif origin in [dict, Dict]:
        if args and len(args) == 2:
            key_result = parse_field_type(args[0])
            value_result = parse_field_type(args[1])
            display = f'Dict[{key_result["display"]}, {value_result["display"]}]'
            types = key_result["types"] + value_result["types"]
            return {"display": display, "types": types}
        else:
            return {"display": "Dict", "types": []}
    else:
        if hasattr(field_type, "__name__"):
            type_name = field_type.__name__
            return {"display": type_name, "types": [type_name]}
        else:
            return {"display": str(field_type), "types": []}


def build_class_map(module_names: List[str]) -> Dict[str, Dict]:  # noqa C901
    """
    Build a mapping of classes to their fields, types, default values, and origin.

    Args:
        module_names (List[str]): List of module names to process.

    Returns:
        Dict[str, Dict]: A mapping of class names to their metadata.
    """
    class_map = {}
    visited_classes = set()

    def process_class(cls: Any) -> None:
        class_name = cls.__name__
        if class_name in visited_classes or class_name in BUILTIN_TYPES:
            return
        visited_classes.add(class_name)
        fields = {}
        try:
            # Debug: Print class hierarchy
            print(f"Processing class: {class_name}")
            print(f"Class {class_name} MRO: {[base.__name__ for base in cls.__mro__]}")

            if (
                pydantic
                and issubclass(cls, pydantic.BaseModel)
                and hasattr(cls, "model_fields")
            ):
                # Pydantic v2 model
                print(f"Identified {class_name} as Pydantic BaseModel (v2)")
                annotations = cls.__annotations__
                for field_name, field_type in annotations.items():
                    type_info = parse_field_type(field_type)
                    field_info: FieldInfo = cls.model_fields.get(field_name)
                    if field_info:
                        # Determine if the field has a default value or default factory
                        if field_info.default is not PydanticUndefined:
                            default_value = field_info.default
                            has_default = True
                        else:
                            default_value = None
                            has_default = False
                        fields[field_name] = {
                            "type": type_info,
                            "default": default_value,
                            "has_default": has_default,
                        }
                        print(
                            f"Processed Pydantic field: {class_name}.{field_name} = {default_value} \
                                (has_default={has_default})"
                        )
                    else:
                        fields[field_name] = {
                            "type": type_info,
                            "default": None,
                            "has_default": False,
                        }
                        print(
                            f"Processed Pydantic field: {class_name}.{field_name} = None (has_default=False)"
                        )
            elif dataclasses.is_dataclass(cls):
                # Dataclass
                print(f"Identified {class_name} as Dataclass")
                for field in dataclasses.fields(cls):
                    field_name = field.name
                    field_type = field.type
                    type_info = parse_field_type(field_type)
                    default_value = (
                        field.default
                        if field.default is not dataclasses.MISSING
                        else None
                    )
                    has_default = field.default is not dataclasses.MISSING
                    fields[field_name] = {
                        "type": type_info,
                        "default": default_value,
                        "has_default": has_default,
                    }
                    print(
                        f"Processed Dataclass field: {class_name}.{field_name} = {default_value} \
                            (has_default={has_default})"
                    )
            elif issubclass(cls, Enum):
                # Enum class
                print(f"Identified {class_name} as Enum")
                # Extract enum members
                members = list(cls.__members__.keys())
                for member_name in members:
                    fields[member_name] = {
                        "type": {"display": "", "types": []},
                        "default": None,
                        "has_default": False,
                    }
                    print(f"Processed Enum member: {class_name}.{member_name}")
            else:
                # Regular class - extract public annotations
                print(f"Identified {class_name} as Regular class")
                annotations = getattr(cls, "__annotations__", {})
                for field_name, field_type in annotations.items():
                    type_info = parse_field_type(field_type)
                    default_value = getattr(cls, field_name, None)
                    has_default = hasattr(cls, field_name)
                    fields[field_name] = {
                        "type": type_info,
                        "default": default_value,
                        "has_default": has_default,
                    }
                    print(
                        f"Processed Regular class field: {class_name}.{field_name} = {default_value} \
                            (has_default={has_default})"
                    )
        except Exception as e:
            print(f"Error processing class {class_name}: {e}")

        class_map[class_name] = {
            "fields": fields,
            "module": cls.__module__,
            "local": cls.__module__ in module_names,
            "is_enum": issubclass(cls, Enum),
        }

        # Enums don't have field types to process further
        if not issubclass(cls, Enum):
            # Recursively process field types
            for field_info_others in fields.values():
                for base_type_name in field_info_others["type"]["types"]:
                    if (
                        base_type_name
                        and base_type_name not in visited_classes
                        and base_type_name not in BUILTIN_TYPES
                    ):
                        try:
                            base_cls = None
                            if base_type_name in sys.modules:
                                base_cls = sys.modules[base_type_name]
                            else:
                                # Attempt to get the class from the current module
                                current_module = sys.modules.get(cls.__module__)
                                if current_module and hasattr(
                                    current_module, base_type_name
                                ):
                                    base_cls = getattr(current_module, base_type_name)
                            if base_cls is None:
                                # Attempt to import the class assuming the module name is the same as the class name
                                base_cls = getattr(
                                    importlib.import_module(base_type_name),
                                    base_type_name,
                                )
                            if base_cls:
                                process_class(base_cls)
                        except Exception as e:
                            print(f"Error importing class {base_type_name}: {e}")
                            if base_type_name not in class_map:
                                class_map[base_type_name] = {
                                    "fields": {},
                                    "module": None,
                                    "local": False,
                                    "is_enum": False,
                                }

    # Import the specified modules and process their classes
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == module.__name__:
                    process_class(obj)
        except Exception as e:
            print(f"Failed to import module {module_name}: {e}")

    return class_map


def visualize_schemas(  # noqa C901
    class_map: Dict[str, Dict],
    filename: str = "./genai_myah_chat_service/schema/schema_viz/schemas.png",
) -> None:
    """
    Visualize the class schemas using Graphviz.

    Args:
        class_map (Dict[str, Dict]): The class mapping to visualize.
    """
    G = pgv.AGraph(directed=True, strict=False, rankdir="LR")
    module_colors = {}
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

    # Assign colors to modules
    modules = set(info["module"] for info in class_map.values() if info["module"])
    for module in sorted(modules):
        module_colors[module] = color_palette[color_index % len(color_palette)]
        color_index += 1

    # Create nodes
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info["fields"]
        module = class_info.get("module")
        color = module_colors.get(module, "#CCCCCC")

        if fields:
            if class_info.get("is_enum"):
                # Create a label for the enum node
                label = f"""<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0">
                <TR><TD BGCOLOR="{color}" COLSPAN="1"><B>{class_name} (Enum)</B></TD></TR>"""
                for member_name in fields.keys():
                    label += f"""<TR>
                    <TD>{member_name}</TD>
                    </TR>"""
                label += "</TABLE>>"
                G.add_node(sanitized_class_name, shape="plaintext", label=label)
            else:
                # Create a label for the node with fields
                label = f"""<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0">
                <TR><TD PORT="class_header" BGCOLOR="{color}" COLSPAN="2"><B>{class_name}</B></TD></TR>"""
                for field_name, field_info in fields.items():
                    sanitized_field_name = sanitize_name(field_name)
                    display_type = field_info["type"]["display"]
                    default_value = field_info["default"]
                    has_default = field_info["has_default"]
                    if has_default:
                        # Append default value to type, handling Enum and 'default_factory'
                        if isinstance(default_value, Enum):
                            # Simplified Enum representation: EnumClass.MemberName
                            display_type += f" = {default_value.__class__.__name__}.{default_value.name}"
                        elif default_value == "default_factory":
                            display_type += " = <default_factory>"
                        else:
                            display_type += f" = {repr(default_value)}"
                        print(
                            f"Field '{class_name}.{field_name}' has default: {display_type}"
                        )
                    else:
                        # Indicate no default value
                        print(f"Field '{class_name}.{field_name}' has no default.")
                    label += f"""<TR>
                    <TD>{field_name}</TD>
                    <TD PORT="{sanitized_field_name}_type">{display_type}</TD>
                    </TR>"""
                label += "</TABLE>>"
                G.add_node(sanitized_class_name, shape="plaintext", label=label)
        else:
            # Create a placeholder node
            G.add_node(
                sanitized_class_name, shape="box", style="dashed", label=class_name
            )
        print(f"Added node: {sanitized_class_name}")

    # Create edges
    for class_name, class_info in class_map.items():
        sanitized_class_name = sanitize_name(class_name)
        fields = class_info["fields"]
        for field_name, field_info in fields.items():
            for base_type_name in field_info["type"]["types"]:
                if base_type_name and base_type_name not in BUILTIN_TYPES:
                    sanitized_base_type = sanitize_name(base_type_name)
                    sanitized_field_name = sanitize_name(field_name)
                    if sanitized_base_type in G.nodes():
                        # Use tailport and headport for proper edge positioning
                        G.add_edge(
                            sanitized_class_name,
                            sanitized_base_type,
                            tailport=f"{sanitized_field_name}_type",
                            headport="class_header",
                            arrowhead="normal",
                        )
                        print(
                            f"Adding edge from '{sanitized_class_name}:{sanitized_field_name}_type' \
                                to '{sanitized_base_type}'"
                        )
                    else:
                        print(
                            f"Warning: Node '{sanitized_base_type}' does not exist in the graph."
                        )
                else:
                    print(
                        f"Skipping built-in type '{base_type_name}' or unresolved type."
                    )

    # Add legend node
    legend_label = """<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0">
    <TR><TD COLSPAN="2"><B>Legend</B></TD></TR>"""

    for module, color in module_colors.items():
        legend_label += f"""<TR>
        <TD BGCOLOR="{color}">&nbsp;&nbsp;&nbsp;&nbsp;</TD>
        <TD>{module}</TD>
        </TR>"""
    legend_label += "</TABLE>>"

    G.add_node("Legend", shape="plaintext", label=legend_label)
    G.add_edge("Legend", "Legend", style="invis", constraint="false")

    # Adjust graph attributes
    G.graph_attr.update(label="Schemas Diagram", labelloc="t")

    # Render the graph
    G.layout(prog="dot")
    G.draw(filename)
    print("\nGraph Nodes:")
    for node in G.nodes():
        print(node)

    print("\nGraph Edges:")
    for edge in G.edges():
        print(edge)


def main() -> None:
    """Define all schemas module and run main function."""
    # Specify the modules you want to include
    module_names = [
        "schemas.comment",
        # Add other modules as needed
    ]

    class_map = build_class_map(module_names)
    visualize_schemas(class_map)


if __name__ == "__main__":
    main()
