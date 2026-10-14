# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import json


@allow_storage
@dataclass
class DisputeRecord:
    dispute_id: str
    claimant: str
    respondent: str
    claimant_evidence_url: str
    respondent_evidence_url: str
    dispute_rules: str
    verdict: str
    liability_split: bigint
    confidence: bigint
    rationale: str
    resolved_index: bigint


def get_confidence_band(score: int) -> int:
    if score < 40:
        return 0
    elif score < 80:
        return 1
    return 2


def get_liability_band(split: int) -> int:
    if split >= 75:
        return 4
    elif split > 55:
        return 3
    elif split >= 45:
        return 2
    elif split > 25:
        return 1
    return 0


class Contract(gl.Contract):
    disputes: TreeMap[str, DisputeRecord]
    party_latest_dispute: TreeMap[str, str]
    total_disputes: bigint
    favor_claimant_count: bigint
    favor_respondent_count: bigint
    split_equal_count: bigint

    def __init__(self):
        self.total_disputes = bigint(0)
        self.favor_claimant_count = bigint(0)
        self.favor_respondent_count = bigint(0)
        self.split_equal_count = bigint(0)

    @gl.public.write
    def file_dispute(
        self,
        claimant: str,
        respondent: str,
        claimant_evidence_url: str,
        respondent_evidence_url: str,
        dispute_rules: str,
    ) -> str:
        # Validate inputs
        if not claimant or not isinstance(claimant, str):
            raise gl.vm.UserError("Invalid input: claimant must be a non-empty string")
        clean_claimant = claimant.strip()
        if len(clean_claimant) == 0:
            raise gl.vm.UserError("Invalid input: claimant cannot be empty")

        if not respondent or not isinstance(respondent, str):
            raise gl.vm.UserError("Invalid input: respondent must be a non-empty string")
        clean_respondent = respondent.strip()
        if len(clean_respondent) == 0:
            raise gl.vm.UserError("Invalid input: respondent cannot be empty")

        if clean_claimant == clean_respondent:
            raise gl.vm.UserError(
                "Invalid input: claimant and respondent cannot be the same address/identity"
            )

        if not claimant_evidence_url or not isinstance(claimant_evidence_url, str):
            raise gl.vm.UserError(
                "Invalid input: claimant_evidence_url must be a non-empty string"
            )
        clean_c_url = claimant_evidence_url.strip()
        if len(clean_c_url) == 0:
            raise gl.vm.UserError(
                "Invalid input: claimant_evidence_url cannot be empty"
            )
        if not (clean_c_url.startswith("http://") or clean_c_url.startswith("https://")):
            raise gl.vm.UserError(
                "Invalid input: claimant_evidence_url must start with http:// or https://"
            )

        if not respondent_evidence_url or not isinstance(respondent_evidence_url, str):
            raise gl.vm.UserError(
                "Invalid input: respondent_evidence_url must be a non-empty string"
            )
        clean_r_url = respondent_evidence_url.strip()
        if len(clean_r_url) == 0:
            raise gl.vm.UserError(
                "Invalid input: respondent_evidence_url cannot be empty"
            )
        if not (clean_r_url.startswith("http://") or clean_r_url.startswith("https://")):
            raise gl.vm.UserError(
                "Invalid input: respondent_evidence_url must start with http:// or https://"
            )

        if not dispute_rules or not isinstance(dispute_rules, str):
            raise gl.vm.UserError("Invalid input: dispute_rules must be a non-empty string")
        clean_rules = dispute_rules.strip()
        if len(clean_rules) == 0:
            raise gl.vm.UserError("Invalid input: dispute_rules cannot be empty")

        # Capture arguments into local variables before entering closures
        target_claimant = clean_claimant
        target_respondent = clean_respondent
        target_c_url = clean_c_url
        target_r_url = clean_r_url
        target_rules = clean_rules

        def leader_fn() -> dict:
            # 1. Fetch claimant evidence
            try:
                raw_c_text = gl.nondet.web.render(target_c_url, mode="text")
            except Exception as exc:
                raise gl.vm.UserError(
                    f"Failed to fetch claimant evidence from URL: {str(exc)}"
                )

            if not raw_c_text or len(raw_c_text.strip()) == 0:
                raise gl.vm.UserError("Claimant evidence URL returned empty content")

            clean_c_sample = (
                raw_c_text.encode("ascii", errors="ignore").decode("ascii")[:4000]
            )

            # 2. Fetch respondent evidence
            try:
                raw_r_text = gl.nondet.web.render(target_r_url, mode="text")
            except Exception as exc:
                raise gl.vm.UserError(
                    f"Failed to fetch respondent evidence from URL: {str(exc)}"
                )

            if not raw_r_text or len(raw_r_text.strip()) == 0:
                raise gl.vm.UserError("Respondent evidence URL returned empty content")

            clean_r_sample = (
                raw_r_text.encode("ascii", errors="ignore").decode("ascii")[:4000]
            )

            # 3. Build arbitration prompt
            prompt_text = (
                "You are an impartial, decentralized arbitration court and jury adjudicating a contract dispute.\n\n"
                f"Claimant: {target_claimant}\n"
                f"Respondent: {target_respondent}\n"
                f"Governing Agreement / Dispute Rules:\n{target_rules}\n\n"
                f"Claimant Evidence (first 4000 chars):\n{clean_c_sample}\n\n"
                f"Respondent Evidence (first 4000 chars):\n{clean_r_sample}\n\n"
                "Evaluate both parties' claims strictly under the governing dispute rules.\n"
                "Determine the liability and fault allocation. You must return ONLY a JSON object with this exact schema:\n"
                "{\n"
                '  "verdict": "FAVOR_CLAIMANT" | "FAVOR_RESPONDENT" | "SPLIT_EQUAL",\n'
                '  "liability_split": <integer 0-100>,\n'
                '  "confidence": <integer 0-100>,\n'
                '  "rationale": "<concise judicial justification explaining which party provided superior evidence>"\n'
                "}\n\n"
                "Verdict definitions:\n"
                "- FAVOR_CLAIMANT: Claimant fulfilled obligations; respondent breached or failed terms.\n"
                "- FAVOR_RESPONDENT: Respondent provided sufficient proof of performance or claimant acted in bad faith.\n"
                "- SPLIT_EQUAL: Both parties share mutual partial fault or evidence is equiponderant.\n\n"
                "Liability split definition:\n"
                "- Percentage of funds/allocation awarded to Claimant (0 to 100).\n"
                "- 100 = 100% claimant favored, 0 = 100% respondent favored, 50 = 50/50 split.\n"
                "Ensure all scores are integers between 0 and 100. Return valid JSON only with no commentary."
            )

            try:
                llm_output = gl.nondet.exec_prompt(
                    prompt_text, response_format="json"
                )
            except Exception as exc:
                raise gl.vm.UserError(f"LLM execution error: {str(exc)}")

            try:
                if isinstance(llm_output, dict):
                    parsed = llm_output
                else:
                    parsed = json.loads(llm_output)
            except Exception:
                raise gl.vm.UserError("Unparseable LLM output: response is not valid JSON")

            if not isinstance(parsed, dict):
                raise gl.vm.UserError(
                    "Unparseable LLM output: root element must be a dictionary"
                )

            verdict_val = str(parsed.get("verdict", "")).strip().upper()
            if verdict_val not in ("FAVOR_CLAIMANT", "FAVOR_RESPONDENT", "SPLIT_EQUAL"):
                raise gl.vm.UserError(
                    f"Invalid verdict from LLM: '{verdict_val}'. Expected FAVOR_CLAIMANT, FAVOR_RESPONDENT, or SPLIT_EQUAL"
                )

            def sanitize_int(key_name: str, fallback: int = 50) -> int:
                val = parsed.get(key_name, fallback)
                try:
                    num = int(val)
                    if num < 0:
                        return 0
                    if num > 100:
                        return 100
                    return num
                except (ValueError, TypeError):
                    return fallback

            split_val = sanitize_int("liability_split", 50)
            confidence_val = sanitize_int("confidence", 50)

            raw_rationale = str(parsed.get("rationale", "No rationale provided"))
            rationale_val = (
                raw_rationale.encode("ascii", errors="ignore").decode("ascii")[:1000]
            )

            return {
                "verdict": verdict_val,
                "liability_split": split_val,
                "confidence": confidence_val,
                "rationale": rationale_val,
            }

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False

            leader_data = leader_res.calldata
            if not isinstance(leader_data, dict):
                return False

            leader_verdict = leader_data.get("verdict")
            leader_split = leader_data.get("liability_split")
            leader_conf = leader_data.get("confidence")

            if leader_verdict not in (
                "FAVOR_CLAIMANT",
                "FAVOR_RESPONDENT",
                "SPLIT_EQUAL",
            ):
                return False
            if not isinstance(leader_split, int) or not isinstance(leader_conf, int):
                return False

            try:
                validator_data = leader_fn()
            except Exception:
                return False

            if not isinstance(validator_data, dict):
                return False

            validator_verdict = validator_data.get("verdict")
            validator_split = validator_data.get("liability_split")
            validator_conf = validator_data.get("confidence")

            if validator_verdict not in (
                "FAVOR_CLAIMANT",
                "FAVOR_RESPONDENT",
                "SPLIT_EQUAL",
            ):
                return False
            if not isinstance(validator_split, int) or not isinstance(validator_conf, int):
                return False

            # Semantic Consensus Rule 1: Verdict match
            if leader_verdict != validator_verdict:
                return False

            # Semantic Consensus Rule 2: Liability band match and 15% tolerance
            if get_liability_band(leader_split) != get_liability_band(validator_split):
                return False
            if abs(leader_split - validator_split) > 15:
                return False

            # Semantic Consensus Rule 3: Confidence band match
            if get_confidence_band(leader_conf) != get_confidence_band(validator_conf):
                return False

            return True

        adjudication = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        # Deterministic state commit
        next_index = int(self.total_disputes) + 1
        dispute_id = f"DISPUTE-{next_index:06d}"
        v_str = str(adjudication["verdict"])

        dispute_rec = DisputeRecord(
            dispute_id=dispute_id,
            claimant=target_claimant,
            respondent=target_respondent,
            claimant_evidence_url=target_c_url,
            respondent_evidence_url=target_r_url,
            dispute_rules=target_rules,
            verdict=v_str,
            liability_split=bigint(adjudication["liability_split"]),
            confidence=bigint(adjudication["confidence"]),
            rationale=str(adjudication["rationale"]),
            resolved_index=bigint(next_index),
        )

        self.disputes[dispute_id] = dispute_rec
        self.party_latest_dispute[target_claimant] = dispute_id
        self.party_latest_dispute[target_respondent] = dispute_id
        self.total_disputes = bigint(next_index)

        if v_str == "FAVOR_CLAIMANT":
            self.favor_claimant_count = bigint(int(self.favor_claimant_count) + 1)
        elif v_str == "FAVOR_RESPONDENT":
            self.favor_respondent_count = bigint(int(self.favor_respondent_count) + 1)
        elif v_str == "SPLIT_EQUAL":
            self.split_equal_count = bigint(int(self.split_equal_count) + 1)

        return dispute_id

    @gl.public.view
    def get_dispute(self, dispute_id: str) -> str:
        if dispute_id not in self.disputes:
            raise gl.vm.UserError(f"Dispute record not found for id: {dispute_id}")
        rec = self.disputes[dispute_id]
        payload = {
            "dispute_id": rec.dispute_id,
            "claimant": rec.claimant,
            "respondent": rec.respondent,
            "claimant_evidence_url": rec.claimant_evidence_url,
            "respondent_evidence_url": rec.respondent_evidence_url,
            "dispute_rules": rec.dispute_rules,
            "verdict": rec.verdict,
            "liability_split": int(rec.liability_split),
            "confidence": int(rec.confidence),
            "rationale": rec.rationale,
            "resolved_index": int(rec.resolved_index),
        }
        return json.dumps(payload)

    @gl.public.view
    def get_dispute_count(self) -> int:
        return int(self.total_disputes)

    @gl.public.view
    def get_dispute_status(self, dispute_id: str) -> str:
        if dispute_id not in self.disputes:
            raise gl.vm.UserError(f"Dispute record not found for id: {dispute_id}")
        return self.disputes[dispute_id].verdict

    @gl.public.view
    def has_dispute(self, dispute_id: str) -> bool:
        return dispute_id in self.disputes

    @gl.public.view
    def get_dispute_liability_split(self, dispute_id: str) -> int:
        if dispute_id not in self.disputes:
            raise gl.vm.UserError(f"Dispute record not found for id: {dispute_id}")
        return int(self.disputes[dispute_id].liability_split)

    @gl.public.view
    def get_party_latest_dispute(self, party: str) -> str:
        if party not in self.party_latest_dispute:
            return ""
        return self.party_latest_dispute[party]

    @gl.public.view
    def get_statistics(self) -> str:
        payload = {
            "total_disputes": int(self.total_disputes),
            "favor_claimant_count": int(self.favor_claimant_count),
            "favor_respondent_count": int(self.favor_respondent_count),
            "split_equal_count": int(self.split_equal_count),
        }
        return json.dumps(payload)
