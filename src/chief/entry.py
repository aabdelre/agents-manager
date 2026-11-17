"""Direct source launcher; works even when macOS hides editable-install .pth files."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chief.cli import main

if __name__ == "__main__":
    main()
