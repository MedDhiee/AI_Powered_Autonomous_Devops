import logging
import sys


class HumanInTheLoop:
    def __init__(self):
        self.logger = logging.getLogger("HITL")

    def request_approval(self, data: dict, env: str) -> bool:
        self.logger.info(f"Initiating request to Human-In-The-Loop (HITL) for deployment in {env}")

        # Non-interactive session (piped stdin, CI runner, VS Code terminal without TTY)
        if not sys.stdin.isatty():
            self.logger.warning(
                "Non-interactive session — deployment HITL skipped. "
                "Pass require_approval=False or run interactively to approve."
            )
            return False

        print("\n--- DEPLOYMENT APPROVAL REQUIRED ---")
        print(f"Target Environment: {env}")
        print(f"Services to Deploy: {[s.get('name') for s in data.get('services', [])]}")
        print("Commands to execute:\n- Vault Secret Decryption\n- Helm Install / Terraform Apply\n- Kubernetes Probes\n")
        try:
            answer = input("Do you approve this deployment plan and the commands? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            self.logger.warning("No interactive input available; deployment rejected by default.")
            return False
        return answer in {"y", "yes"}
