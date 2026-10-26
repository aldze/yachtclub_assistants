"""Чат с ассистентом в терминале.

    python -m app.channels.cli --assistant sales

Команды: /reset — новый диалог, /switch <id> — сменить ассистента (история сохраняется),
/prompt — размер системного промпта, /help, /exit.
"""
from __future__ import annotations

import argparse
import sys

from app.chat import Conversation
from app.config import ConfigError, load_env, load_passports
from app.llm import LLMError

HELP = "Команды: /reset — новый диалог, /switch <id> — сменить ассистента, /prompt — размер промпта, /exit — выход"


def print_intro(conv: Conversation) -> None:
    p = conv.passport
    print(f"\n=== {p.name} — {p.title} ({p.id}, {p.provider}/{p.model}) ===")
    greeting = conv.greet()
    if greeting:
        print(f"{p.name}: {greeting}")
    if p.conversation_starters:
        print("Например: " + " | ".join(p.conversation_starters))


def handle_command(conv: Conversation, line: str) -> bool:
    """Выполняет команду. Возвращает False, если пора выходить."""
    cmd, _, arg = line.partition(" ")
    if cmd in ("/exit", "/quit"):
        return False
    if cmd == "/reset":
        conv.reset()
        print("Диалог начат заново.")
        print_intro(conv)
    elif cmd == "/switch":
        try:
            conv.switch(arg.strip())
        except KeyError as e:
            print(e.args[0])
        else:
            print_intro(conv)
    elif cmd == "/prompt":
        text = conv.system_prompt()
        print(f"Системный промпт {conv.active}: {len(text)} знаков, ~{len(text) // 3} токенов")
    else:
        print(HELP)
    return True


def main() -> int:
    load_env()
    passports = load_passports()
    ap = argparse.ArgumentParser(description="Чат с ассистентом в терминале")
    ap.add_argument("--assistant", default="sales", choices=list(passports), help="ассистент (по умолчанию sales)")
    args = ap.parse_args()

    try:
        conv = Conversation(args.assistant, passports)
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        return 1

    print(HELP)
    print_intro(conv)
    while True:
        try:
            line = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.startswith("/"):
            if not handle_command(conv, line):
                return 0
            continue
        try:
            reply = conv.send(line)
        except LLMError as e:
            print(f"[ошибка] {e}")
            continue
        print(f"\n{conv.passport.name}: {reply.text}")


if __name__ == "__main__":
    sys.exit(main())
