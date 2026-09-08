"""Absolute-import entry point for the Windows portable executable."""
from ai_cull_assistant.app import main

if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from ai_cull_assistant.self_test import run

        run(sys.argv[2])
    else:
        main()
