# Implementation plan: setup wizard

Scope: [approved wizard requirements](SPEC.md).

1. Reuse helper transport, version gate and site collection; implement trust and
   input handling in a Python module with a Bash entry point. (REQ-001–005)
2. Add truthful bar enablement/placement and an agent interface. (REQ-006–007)
3. Test certificate refusal, credential handling, site selection, configuration
   preservation and shell failures using isolated fixtures. (AC-001–007)
4. Review security and setup usability, reconcile concrete findings, run the
   existing regression checks and document remaining limitations.
5. Apply only wizard, tests and documentation changes to the working checkout.
   Preserve unrelated local edits. Do not install into the live plugin directory.

The existing configuration script remains responsible for atomic writes and
reload rollback. Certificate files must not overwrite a previously trusted file
before the configuration transaction succeeds.
