# Someone else's ecosystem

Where your application would actually run, inside your actual firm.

> **Week 1 starts this file. Week 9 finishes it.**
> TC1 Step 6 asks eight questions. The full Week 9 checklist has around thirty,
> and the eight are a strict subset — so nothing here gets thrown away.

Describe patterns, not specifics. "On-prem OpenShift, images from an internal
registry, no public egress" is useful to everyone and identifies nobody. Never
put real hostnames, IPs, or architecture diagrams in this file.

---

## Week 1 — the first eight questions

<!-- Answered in TC1 Step 6. Bring these back from a real conversation. -->

**Who did you ask?** MLOps Engineer

<!-- your answer here -->

| # | Question | Answer |
| :---: | --- | --- |
| 1 | Where do internal apps run? (k8s, ECS, App Service, a VM someone maintains) | We use OpenShift for deployment right now. |
| 2 | Which cloud? AWS, Azure, GCP, on-prem, several? | Azure |
| 3 | Is there an internal container registry? | Yes |
| 4 | How do users log in? (Okta, Entra ID, Ping, SAML, homegrown) | EntraID |
| 5 | Can the app reach the internet? Egress proxy? Allowlist? | This will not be exposed to the internet |
| 6 | Where do secrets come from? (Vault, Secrets Manager, Key Vault) | Azure Key Vault |
| 7 | Who approves a deployment, and what review does it need? | The MLOps Engineer |
| 8 | Is there an approved internal LLM endpoint already? | The Enterprise AI Team (of which I am a part) manages api keys and LLM endpoints. |

**What surprised you?**

Nothing was surprising, I was aware of the fact that we have this infrastructure available.

**What would you have to change about your app to deploy there?**

There is a specific copier template we use for helm and openshift deployments that this codebase will have to be migrated into. This is something we use claude for regularly.

---

## Week 1 — what your machine told you

The test returned 7/7 reachable.

| host |	why	| status |	signed by |
|---|---|---|---|
pypi.org |	Python packages |	OK | GlobalSign nv-sa|
files.pythonhosted.org	| the actual wheel downloads |	OK	| GlobalSign nv-sa|
api.openai.com |	the model API |	OK	| Google Trust Services|
cdn.jsdelivr.net	| the CDN the Week 1 frontend uses  |	OK |	GlobalSign nv-sa|
huggingface.co	|open weights, Weeks 2 and 8|	OK|	Amazon|
registry-1.docker.io|	container images|	OK	|Amazon|
github.com|	this repository	|OK	|Sectigo Limited|

Verdict: Open network, public CAs, no proxy. Nothing here will bite you.

**Does this match what you were told in the table above?** 

Yes, with the caveat that I will be running the model locally.

---

## Week 9 — the full checklist

<!--
  TC9 extends this same file. Do not start a new one: the point is that the git
  history shows one continuous piece of work from Week 1 to Week 9.
-->

<!-- Week 9 -->
