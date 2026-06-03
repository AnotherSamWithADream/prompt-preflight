"""``python -m prompt_enhancer`` -- equivalent to the ``enhance`` launcher.

Routes to the launcher so the package is usable without the console scripts on PATH:

    python -m prompt_enhancer "make my code faster"      # == enhance "..."
    python -m prompt_enhancer --serve-only               # run just the proxy

For the rewrite-to-clipboard CLI or the hook, use ``enhance-cli`` / ``enhance-hook``
(or ``python -m prompt_enhancer.cli`` / ``python -m prompt_enhancer.hook``).
"""

import sys

from prompt_enhancer.launcher import main

if __name__ == "__main__":
    sys.exit(main())
