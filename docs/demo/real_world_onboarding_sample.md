# Real-World Demo Sample

Use [real_world_onboarding_sample.json](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/real_world_onboarding_sample.json) as the default demo dataset for local testing.

It is designed to match the current branch behavior:

- tenant initialization with `tenant_key`
- platform realm plus per-tenant realm setup in Keycloak
- Temporal-backed verification execution
- self-service online onboarding
- agent-assisted offline onboarding with deferred verification
- phone OTP
- Fayda OTP and identity pull
- fuzzy name matching
- KYC decision output and submission search filters

## What Is Included

- a realistic tenant payload
- platform admin and tenant admin credentials for the local demo
- a product payload
- a tenant template `rules_config` with:
  - `submission_search`
  - `verification_flow`
  - realistic decision rules
- two realistic submissions:
  - an individual digital onboarding case
  - an agent-assisted business-owner case

## Recommended Usage

1. Run [bootstrap_keycloak_local.sh](/Users/berhanu.tarekegn/git/onboarding-and-verification/scripts/bootstrap_keycloak_local.sh)
2. Follow [end_to_end_product_simulation.md](/Users/berhanu.tarekegn/git/onboarding-and-verification/docs/demo/end_to_end_product_simulation.md)
3. Use the sample tenant payload when creating the tenant
4. Use `template_version.rules_config` when creating the tenant template definition
5. Use the submission payloads in order to exercise:
   - immediate self-service verification
   - deferred onboarding, then later verification

## Expected Demo Outcomes

- `Self-service individual customer` should end with:
  - `decision=approved`
  - `kyc_level=level_2`
- `Agent-assisted deferred onboarding` should:
  - start in `pending`
  - resume later through Temporal
  - end with `decision=approved`
  - `kyc_level=level_2`

## Current Scope

The sample data is intentionally aligned to the currently implemented runtime, so it uses the working demo adapters:

- `demo_phone_otp`
- `demo_fayda_otp`
- `demo_fuzzy_match`

Trade-license and other asynchronous provider checks are not yet executed by the runtime, even though the data model and flow design support adding them later.
