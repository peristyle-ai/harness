The shipped `config.yaml.example` set `agent.max_tool_iterations: 25`, while the production install has always run 500.

The shipped default of 25 truncates a legitimate long turn: an agent hit the cap and was told to break the request into smaller steps, instead of being allowed to finish the work it had started.

This changes the example only -- one file, one concern -- so a fresh install starts on the value the production install actually uses.
