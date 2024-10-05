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

def get_local_modules(project_root):
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
    local_modules = get_local_modules(project_root)
    class_map = {}
    visited_classes = set()
    visited_modules = set()
    
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

        # Determine if class is local
        module = sys.modules.get(cls.__module__)
        if module:
            module_file = getattr(module, '__file__', None)
            if module_file:
                is_local = os.path.commonpath([os.path.realpath(module_file), os.path.realpath(project_root)]) == os.path.realpath(project_root)
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

        # Enums don't have field types to process further
        if not issubclass(cls, Enum):
            # Recursively process field types
            for field_info in fields.values():
                for base_type_name in field_info['types']:
                    if base_type_name and base_type_name not in visited_classes and base_type_name not in BUILTIN_TYPES:
                        try:
                            base_cls = None
                            for mod in sys.modules.values():
                                if hasattr(mod, base_type_name):
                                    base_cls = getattr(mod, base_type_name)
                                    break
                            if not base_cls:
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

    for module_name in local_modules:
        if module_name in visited_modules:
            continue
        visited_modules.add(module_name)
        try:
            module = importlib.import_module(module_name)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == module.__name__:
                    process_class(obj)
        except Exception as e:
            print(f"Failed to import module {module_name}: {e}")
    return class_map

def visualize_schemas(class_map: Dict[str, Dict]):
    # ... existing visualization code ...
    # (No changes needed here)
    pass  # Replace with the existing code from previous snippets

def main():
    project_root = './schemas/'  # Replace with your project's root directory
    sys.path.insert(0, project_root)
    class_map = build_class_map(project_root)
    visualize_schemas(class_map)

if __name__ == '__main__':
    main()
