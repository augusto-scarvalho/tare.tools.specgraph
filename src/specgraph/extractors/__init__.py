"""Optional AST extractors; importing SpecGraph never requires their wheels."""
from .tree_sitter import TreeSitterCodeParser, language_capabilities, structural_edges

__all__ = ["TreeSitterCodeParser", "language_capabilities", "structural_edges"]
