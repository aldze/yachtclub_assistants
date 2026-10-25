#!/usr/bin/env python3
"""Заполняет оглавление в .docx через LibreOffice (нужен установленный LibreOffice с python-uno).

    python tools/update_toc.py build/Нейроассистенты_яхты.docx

Без этого шага оглавление тоже работает: Word предложит обновить поля при открытии.
"""
import subprocess
import sys
import time
from pathlib import Path

import uno  # noqa: E402  (входит в LibreOffice)
from com.sun.star.beans import PropertyValue  # noqa: E402


def prop(name, value):
    p = PropertyValue(); p.Name = name; p.Value = value; return p


def main(path: str) -> None:
    src = Path(path).resolve()
    port = 2002
    proc = subprocess.Popen(["soffice", "--headless", "--invisible", "--norestore",
                             f"--accept=socket,host=localhost,port={port};urp;"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        for _ in range(60):
            try:
                ctx = resolver.resolve(f"uno:socket,host=localhost,port={port};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("LibreOffice не запустился")
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        doc = desktop.loadComponentFromURL(src.as_uri(), "_blank", 0, (prop("Hidden", True),))
        indexes = doc.getDocumentIndexes()
        count = indexes.getCount()
        for i in range(count):
            indexes.getByIndex(i).update()
        doc.storeToURL(src.as_uri(), (prop("FilterName", "MS Word 2007 XML"),))
        doc.close(True)
        print(f"Оглавление обновлено: {src.name} ({count} шт.)")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main(sys.argv[1])
