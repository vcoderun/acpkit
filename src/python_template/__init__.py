"""
A module.
"""

__version__ = "0.1.0"


def say_hello(name: str) -> None:
    print(f"Hello, {name.title()}!")


__all__ = ["__version__", "say_hello"]
