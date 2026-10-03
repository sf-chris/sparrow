import sys

if len(sys.argv) > 2 and sys.argv[1] == "--subtitle-worker":
    from backend.agents.subtitle_worker import main

    raise SystemExit(main(sys.argv[2]))
if len(sys.argv) > 2 and sys.argv[1] == "--subtitle-evidence":
    from backend.agents.subtitle_evidence import main

    raise SystemExit(main(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None))
if len(sys.argv) == 1:
    from backend.agents.node_setup import main
else:
    from backend.agents.node_client import main
main()
