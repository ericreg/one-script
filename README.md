# one-script

Turns a small Python package into one readable `.py` file. This is useful for certain embedded python environments that expect you to provide a single python script. With this script you can follow standard development practices while still being able to produce a singular script.  The finished file is automatically formatted with [Ruff](https://docs.astral.sh/ruff/formatter/).

The code uses the python's built in [abstract syntax tree](https://docs.python.org/3/library/ast.html) to analyze your python code. This along with some basic compiler style logic, and we can reliably parse and analyze a python package.

## Quick start

You need **Python 3.13 or newer**. From a checkout of this repository, install:

```bash
python -m pip install .
```



Suppose you have this small package

```text
work/
└── example_pkg/
    ├── __init__.py
    ├── config.py
    └── greeter.py
```

From `work/`, combine it into one file:

```bash
one-script ./example_pkg example_pkg -o bundled.py
```

To skip files, add `--exclude` with a quoted pattern. Repeat it for more patterns:

```bash
one-script ./example_pkg example_pkg -o bundled.py --exclude 'test_*.py'
```

Run `one-script --help` for options. If the command is not on your `PATH`, use
`python -m one_script` in its place.

## Use of AI

Most of the code was written by AI, but was designed and tested by a human. It has received quite a bit of use and I find I no longer have a need to keep iterating and tweaking to program. This tool has been useful for my own purposes so I am open sourcing it here. 


## Limitations

Combining a package into one file changes how its code is organized. Some
packages need changes before they can be bundled.

### Package structure and names

| Limitation | Details |
| --- | --- |
| Names must be unique across the package | Two source files cannot define the same global name, even for private helpers or constants. Imported names must not conflict with other imports or definitions. |
| Keep `__init__.py` files simple | They may contain imports and an opening docstring. Move functions, classes, constants, and assignments such as `__version__` into other files. |
| Keep startup code outside the package | At the top level of each source file, use imports followed by functions, classes, and simple assignments such as `LIMIT = 10`. Top-level loops, conditionals, standalone calls, and `if __name__ == "__main__":` blocks are unsupported. Ordinary control flow inside functions and methods is fine. |
| Some assignment forms are unsupported | Avoid chained assignments (`A = B = 1`), unpacking at module level (`A, B = values`), declarations without values (`LIMIT: int`), and assignment expressions (`:=`). Repeated assignments to a global name are rejected, though updates such as `TOTAL += STEP` are supported after an initial assignment in the same file. |

### Imports and dependencies

| Limitation | Details |
| --- | --- |
| Import specific names from your own package | Use `from .models import Worker` or `from example_pkg.models import Worker`. Importing an internal module, renaming an internal import, and wildcard imports such as `from module import *` are unsupported. Internal imports must be at module level, before definitions. |
| Use relative or package-qualified imports for your own code | A bare import such as `from models import Worker` is treated as an external dependency. Excluding a file that another included file imports causes an error. |
| External dependencies still need to be installed | The output contains your Python source, not third-party libraries, compiled extensions, data files, or a standalone executable. |
| Some initialization dependencies cannot be resolved | For example, two files whose constants depend on each other may need to be reorganized. Definitions keep their order within each source file. |

### Behavior and compatibility

| Limitation | Details |
| --- | --- |
| Code that relies on separate modules may behave differently | Dynamic imports, package-relative data files, `__file__`, `__package__`, and saved objects that refer to original module names are outside the supported model. Shared mutable state and code that depends on import order also need care. |
| Use the intended Python version | The tool does not translate code for older Python versions. Files containing definitions must use the same `__future__` features, such as `from __future__ import annotations`. |
| Formatting follows Ruff's defaults | Spacing, quotes, and line breaks may change. Module docstrings are replaced by a bundle header, and comments attached to imports are dropped. |
