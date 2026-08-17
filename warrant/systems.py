"""The real systems a gateway may be granted, and the dry run that stands in.

A system is just a callable that takes an action string and returns whatever it
returns. Keeping the interface this thin is deliberate: the gateway's job is
deciding whether an action is permitted, and it should not grow opinions about
what n8n or anything else looks like.

Nothing here is reachable unless it was passed to the Gateway constructor. An
ungranted system is a refusal, not a passthrough, which is why this module hands
back a dict of systems rather than registering anything globally.
"""

import json
import os
import urllib.error
import urllib.request


class DryRun:
    """Records what would have happened. The default, on purpose.

    A gateway whose systems are unconfigured should not quietly reach production
    the first time someone runs it. Configuring a real webhook is a decision that
    should have to be made out loud, in the environment.
    """

    def __init__(self, name):
        self.name = name
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        return {"dry_run": True, "system": self.name, "action": action}


class N8nWebhook:
    """Fires one n8n production webhook and returns its response.

    These workflows already park their own outbound actions behind a human gate.
    This gateway sits in front of that, deciding whether the agent was entitled to
    ask at all, so the two guards are independent rather than the same guard twice.
    """

    def __init__(self, name, url, timeout_s=20):
        self.name = name
        self.url = url
        self.timeout_s = timeout_s

    def __call__(self, action):
        payload = json.dumps({"action": action, "source": "warrant"}).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = response.read().decode("utf-8", "replace")
                return {"system": self.name, "status": response.status, "body": body[:800]}
        except urllib.error.HTTPError as failure:
            return {"system": self.name, "status": failure.code, "error": failure.reason}
        except Exception as failure:  # noqa: BLE001 - the caller records the text
            return {"system": self.name, "status": None, "error": str(failure)}


# Environment variable per system. Absent means the dry run stands in, and the
# name still resolves, so a proposal against a configured-but-offline system is
# refused by policy rather than by an accident of deployment.
WEBHOOK_ENV = {
    "n8n:msp-ticket-triage": "WARRANT_N8N_TRIAGE_URL",
    "n8n:msp-review-engine": "WARRANT_N8N_REVIEW_URL",
}


def build_systems():
    """Every system this gateway may reach, real where configured."""
    systems = {}
    for name, env_var in WEBHOOK_ENV.items():
        url = os.environ.get(env_var)
        systems[name] = N8nWebhook(name, url) if url else DryRun(name)
    return systems


def describe(systems):
    return {
        name: ("live" if isinstance(system, N8nWebhook) else "dry run")
        for name, system in sorted(systems.items())
    }
