# Setup wizard decisions

- The user approved the wizard outline and explicitly requested implementation;
  this supplies the product approval for the documented scope and plan.
- The wizard reports unreachable certificate hostnames rather than rewriting
  `/etc/hosts` or resolver configuration.
- Unit tests are already discovered by `tests/run.sh`; no runner change is needed.
- Review and validation results are recorded in implementation-notes.md.
