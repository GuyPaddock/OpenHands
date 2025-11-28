import re
import bashlex
from typing import Any

from openhands.core.logger import openhands_logger as logger


def parse_boolean(value: str) -> bool:
    """Interpret typical environment-style boolean strings.

    Accepted truthy values (case-insensitive):
        "1", "true", "t", "yes", "y", "on"

    Args:
        value: Raw string value (e.g. from os.getenv).

    Returns:
        True if the value is considered truthy, otherwise False.
    """
    return value.lower() in ("1", "true", "t", "yes", "y", "on")


def is_special_key(command: str) -> bool:
    """Check if the command is a special key, of the form C-<key>."""
    return ((stripped_command := command.strip()) and
            stripped_command.startswith('C-') and
            len(stripped_command) == 3
            and stripped_command[2].isalpha())


def split_bash_commands(commands: str) -> list[str]:
    """Parses and splits bash commands using bashlex."""
    if not commands.strip():
        return ['']
    try:
        parsed = bashlex.parse(commands)
    except (bashlex.errors.ParsingError, NotImplementedError, TypeError, AttributeError):
        logger.debug(
            f'Failed to parse bash commands: {commands}. Returning original.', exc_info=True
        )
        return [commands]

    result: list[str] = []
    last_end = 0

    for node in parsed:
        start, end = node.pos
        if start > last_end:
            between = commands[last_end:start]
            if result:
                result[-1] += between.rstrip()

        command = commands[start:end].rstrip()
        result.append(command)
        last_end = end

    remaining = commands[last_end:].rstrip()
    if last_end < len(commands) and result:
        result[-1] += remaining
    elif remaining:
        result.append(remaining)
    return result


def escape_bash_special_chars(command: str) -> str:
    """Escapes characters that have different interpretations in bash vs python.

    Commands pass through three distinct layers of interpretation, each of which might "eat"
    special characters, particularly backslashes:
        1. Python: Stores the command as a string.
        2. Tmux: Receives the string via send-keys. Tmux has its own special characters (like ;
           which separates Tmux commands).
        3. Bash: Finally receives the keystrokes and interprets them.

    Without this: If the user types ls \; (to escape a semicolon), Tmux or the transport layer
    might consume the backslash. Bash would then receive ls ;, effectively running ls followed
    by an empty command, rather than treating the semicolon as an argument.

    With this: The code "escapes the escape," ensuring that the final Bash process receives the
    literal backslash the user intended.
    """
    if command.strip() == '':
        return ''

    try:
        parts = []
        last_pos = 0

        def visit_node(node: Any) -> None:
            nonlocal last_pos
            if (
                node.kind == 'redirect'
                and hasattr(node, 'heredoc')
                and node.heredoc is not None
            ):
                between = command[last_pos: node.pos[0]]
                parts.append(between)
                parts.append(command[node.pos[0]: node.heredoc.pos[0]])
                parts.append(command[node.heredoc.pos[0]: node.heredoc.pos[1]])
                last_pos = node.pos[1]
                return

            if node.kind == 'word':
                between = command[last_pos: node.pos[0]]
                word_text = command[node.pos[0]: node.pos[1]]

                between = re.sub(r'\\([;&|><])', r'\\\\\1', between)
                parts.append(between)

                if (
                    (word_text.startswith('"') and word_text.endswith('"'))
                    or (word_text.startswith("'") and word_text.endswith("'"))
                    or (word_text.startswith('$(') and word_text.endswith(')'))
                    or (word_text.startswith('`') and word_text.endswith('`'))
                ):
                    parts.append(word_text)
                else:
                    word_text = re.sub(r'\\([;&|><])', r'\\\\\1', word_text)
                    parts.append(word_text)

                last_pos = node.pos[1]
                return

            if hasattr(node, 'parts'):
                for part in node.parts:
                    visit_node(part)

        nodes = list(bashlex.parse(command))
        for node in nodes:
            between = command[last_pos: node.pos[0]]
            between = re.sub(r'\\([;&|><])', r'\\\\\1', between)
            parts.append(between)
            last_pos = node.pos[0]
            visit_node(node)

        remaining = command[last_pos:]
        parts.append(remaining)
        return ''.join(parts)
    except (bashlex.errors.ParsingError, NotImplementedError, TypeError):
        logger.debug(f'Failed to escape bash command: {command}', exc_info=True)
        return command
