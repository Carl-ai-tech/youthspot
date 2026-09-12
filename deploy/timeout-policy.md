# Inference timeout policy

The public runtime assigns one request budget: at most 55 seconds, or the Lambda remaining time minus 5 seconds, whichever is lower. Every Bedrock call made by the action shares this ContextVar budget, including any second call. The context resets after the request to avoid warm-Lambda leakage.

Gate acquisition waits at most 18 seconds and leaves at least 8 seconds in the budget. After acquisition, Bedrock recalculates its read timeout from the remaining budget, capped at 48 seconds, reserving 5 seconds for connection (2 seconds maximum), gate release and response serialization. SDK retry count remains one attempt. Calls with less than one second available for reading are rejected before inference.

Read/connection transport failures do not prove remote inference stopped. They return an explicit 504 and retain the distributed 180-second lease until expiry. Normal completed responses and explicit service rejections release with the existing 1.1-second cooldown. A busy gate returns 429. No automatic model retry is introduced.

Limits: SDK socket timeouts are inactivity limits, not a cancellable hard wall-clock deadline. DNS, streaming response trickle, process termination and external account callers are not controlled by this policy. Lambda remains the final 60-second hard limit; its death leaves the existing lease fail-closed. We do not claim the remote model is cancelled or that a 180-second lease proves all remote work ended. Load tests and live fault injection were not performed. Ordinary offline tests cover shrinking budgets across multiple calls, expired-budget rejection, exception translation and no premature lease release after an ambiguous timeout.
