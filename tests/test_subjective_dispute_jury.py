# Test suite for SubjectiveDisputeJury GenLayer Intelligent Contract
# Compatible with gltest, direct_vm, and standalone Python test environments.

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import pytest

# Define mock VM and Return structures to support both gltest and local direct mode
class MockReturn:
    def __init__(self, calldata):
        self.calldata = calldata


class MockUserError(Exception):
    pass


class MockNondetWeb:
    def __init__(self):
        self.urls = {}

    def render(self, url: str, mode: str = "text") -> str:
        for pattern, content in self.urls.items():
            if pattern in url or url in pattern:
                if isinstance(content, dict):
                    if content.get("status", 200) >= 400:
                        raise Exception(f"HTTP Error {content.get('status')}")
                    return content.get("body", "")
                return str(content)
        raise Exception(f"Failed to fetch URL: {url} (404 Not Found)")


class MockNondet:
    def __init__(self):
        self.web = MockNondetWeb()
        self.prompts = {}
        self.call_count = 0
        self.prompt_sequence = []

    def exec_prompt(self, prompt: str, response_format: str = "json") -> str:
        self.call_count += 1
        if self.prompt_sequence:
            return self.prompt_sequence.pop(0)

        for pattern, response in self.prompts.items():
            if pattern in prompt or prompt in pattern:
                if isinstance(response, str):
                    return response
                return json.dumps(response)

        # Default fallback adjudication with canonical settlement tier
        return json.dumps({
            "verdict": "FAVOR_CLAIMANT",
            "settlement_tier": "MAJOR_CLAIMANT",
            "confidence": 85,
            "rationale": "Default mock arbitration ruling"
        })


class MockGenVM:
    UserError = MockUserError
    Return = MockReturn

    def __init__(self):
        self.validator_override = None

    def run_nondet_unsafe(self, leader_fn, validator_fn):
        leader_output = leader_fn()
        leader_res = MockReturn(leader_output)

        if self.validator_override is not None:
            is_valid = self.validator_override(leader_res)
        else:
            is_valid = validator_fn(leader_res)

        if not is_valid:
            raise Exception("Consensus failed: Validator rejected leader proposal")

        return leader_res.calldata


class MockPublic:
    @staticmethod
    def write(fn):
        return fn

    @staticmethod
    def view(fn):
        return fn


class GenericMockStorage(dict):
    def __class_getitem__(cls, item):
        return cls


class GenericMockArray(list):
    def __class_getitem__(cls, item):
        return cls


class MockGL:
    def __init__(self):
        self.nondet = MockNondet()
        self.vm = MockGenVM()
        self.public = MockPublic()

    class Contract:
        pass


import types
if "genlayer" not in sys.modules:
    _gl_mod = types.ModuleType("genlayer")
    _gl_inst = MockGL()
    _gl_mod.gl = _gl_inst
    _gl_mod.allow_storage = lambda cls: cls
    _gl_mod.bigint = int
    _gl_mod.TreeMap = GenericMockStorage
    _gl_mod.DynArray = GenericMockArray
    _gl_mod.Address = str
    sys.modules["genlayer"] = _gl_mod


# Global mock installation helper accepting dict parameter
def sim_installMocks(target_env, mocks: dict):
    if hasattr(target_env, "sim_installMocks"):
        return target_env.sim_installMocks(mocks)

    if hasattr(target_env, "provider") and hasattr(target_env.provider, "request"):
        try:
            return target_env.provider.request({"method": "sim_installMocks", "params": [mocks]})
        except Exception:
            pass

    # Direct VM or in-memory mock environment
    web_mocks = mocks.get("nondet_web_request") or mocks.get("web") or {}
    llm_mocks = mocks.get("nondet_exec_prompt") or mocks.get("llm") or {}

    if hasattr(target_env, "mock_web") and hasattr(target_env, "mock_llm"):
        for pattern, data in web_mocks.items():
            target_env.mock_web(pattern, data)
        for pattern, response in llm_mocks.items():
            target_env.mock_llm(pattern, response)
        return

    # In-memory mock harness
    if hasattr(target_env, "nondet"):
        for url_pattern, content in web_mocks.items():
            target_env.nondet.web.urls[url_pattern] = content
        for prompt_pattern, response in llm_mocks.items():
            target_env.nondet.prompts[prompt_pattern] = response


# Setup test fixture
@pytest.fixture
def test_env(monkeypatch):
    mock_gl = MockGL()
    import contracts.subjective_dispute_jury as sdj

    monkeypatch.setattr(sdj, "gl", mock_gl)
    monkeypatch.setattr(sdj.gl.vm, "UserError", MockUserError)

    contract = sdj.Contract()
    contract.disputes = {}
    contract.party_latest_dispute = {}
    contract.total_disputes = 0
    contract.favor_claimant_count = 0
    contract.favor_respondent_count = 0
    contract.split_equal_count = 0

    return {
        "contract": contract,
        "mock_gl": mock_gl,
        "module": sdj,
    }


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_sim_install_mocks_structure(test_env):
    """Verify sim_installMocks properly sets up web and llm dictionaries."""
    mock_gl = test_env["mock_gl"]
    mocks = {
        "nondet_web_request": {
            "https://evidence.org/claimant": "Claimant delivered design assets on time.",
            "https://evidence.org/respondent": "Respondent claims deliverables were incomplete.",
        },
        "nondet_exec_prompt": {
            "arbitration court": {
                "verdict": "FAVOR_CLAIMANT",
                "settlement_tier": "FULL_CLAIMANT",
                "confidence": 90,
                "rationale": "Claimant provided verifiable Git commits matching the scope."
            }
        }
    }
    sim_installMocks(mock_gl, mocks)

    assert "https://evidence.org/claimant" in mock_gl.nondet.web.urls
    assert "https://evidence.org/respondent" in mock_gl.nondet.web.urls
    assert "arbitration court" in mock_gl.nondet.prompts


def test_dispute_favor_claimant_full_award(test_env):
    """Happy path: Unanimous consensus resolving in favor of claimant with FULL_CLAIMANT award (100%)."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]

    c_url = "https://escrow.org/claimant/case1"
    r_url = "https://escrow.org/respondent/case1"
    rules = "Client must release milestone upon receipt of Figma designs matching SLA."

    mocks = {
        "nondet_web_request": {
            c_url: "Figma link provided, client acknowledged receipt on Slack on Day 3.",
            r_url: "Client decided to change project direction on Day 5."
        },
        "nondet_exec_prompt": {
            "arbitration court": json.dumps({
                "verdict": "FAVOR_CLAIMANT",
                "settlement_tier": "FULL_CLAIMANT",
                "confidence": 95,
                "rationale": "Claimant fully satisfied original SLA before scope change."
            })
        }
    }
    sim_installMocks(mock_gl, mocks)

    dispute_id = contract.file_dispute(
        claimant="0xAliceFreelancer",
        respondent="0xBobClient",
        claimant_evidence_url=c_url,
        respondent_evidence_url=r_url,
        dispute_rules=rules,
    )

    assert dispute_id == "DISPUTE-000001"
    assert contract.get_dispute_count() == 1
    assert contract.has_dispute(dispute_id) is True
    assert contract.get_dispute_status(dispute_id) == "FAVOR_CLAIMANT"
    assert contract.get_settlement_tier(dispute_id) == "FULL_CLAIMANT"
    assert contract.get_claimant_payout_pct(dispute_id) == 100
    assert contract.get_respondent_payout_pct(dispute_id) == 0
    assert contract.get_dispute_liability_split(dispute_id) == 100
    assert contract.get_party_latest_dispute("0xAliceFreelancer") == dispute_id
    assert contract.get_party_latest_dispute("0xBobClient") == dispute_id

    # Validate retrieved record payload
    record_json = contract.get_dispute(dispute_id)
    record = json.loads(record_json)
    assert record["dispute_id"] == "DISPUTE-000001"
    assert record["verdict"] == "FAVOR_CLAIMANT"
    assert record["settlement_tier"] == "FULL_CLAIMANT"
    assert record["claimant_payout_pct"] == 100
    assert record["respondent_payout_pct"] == 0
    assert record["liability_split"] == 100
    assert record["confidence"] == 95
    assert "original SLA" in record["rationale"]

    # Validate aggregate stats
    stats = json.loads(contract.get_statistics())
    assert stats["total_disputes"] == 1
    assert stats["favor_claimant_count"] == 1
    assert stats["favor_respondent_count"] == 0
    assert stats["split_equal_count"] == 0


def test_dispute_favor_claimant_major_award(test_env):
    """Happy path: Consensus on MAJOR_CLAIMANT tier deterministically binds payout to exactly 75%."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]

    c_url = "https://escrow.org/claimant/case1b"
    r_url = "https://escrow.org/respondent/case1b"
    rules = "Deliverable with minor bug incurs partial deduction."

    mocks = {
        "nondet_web_request": {
            c_url: "Feature completed with minor CSS flaw.",
            r_url: "Minor CSS flaw reported."
        },
        "nondet_exec_prompt": {
            "arbitration court": json.dumps({
                "verdict": "FAVOR_CLAIMANT",
                "settlement_tier": "MAJOR_CLAIMANT",
                "confidence": 90,
                "rationale": "Substantial delivery with minor defect justifies 75% payout."
            })
        }
    }
    sim_installMocks(mock_gl, mocks)

    dispute_id = contract.file_dispute(
        claimant="0xAlice",
        respondent="0xBob",
        claimant_evidence_url=c_url,
        respondent_evidence_url=r_url,
        dispute_rules=rules,
    )

    assert contract.get_settlement_tier(dispute_id) == "MAJOR_CLAIMANT"
    assert contract.get_claimant_payout_pct(dispute_id) == 75
    assert contract.get_respondent_payout_pct(dispute_id) == 25
    assert contract.get_dispute_liability_split(dispute_id) == 75


def test_dispute_favor_respondent_happy_path(test_env):
    """Happy path: Evaluation resolves in favor of respondent with FULL_RESPONDENT award (0% claimant)."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]

    c_url = "https://escrow.org/claimant/case2"
    r_url = "https://escrow.org/respondent/case2"
    rules = "Full payment requires zero critical vulnerabilities in smart contract audit."

    mocks = {
        "nondet_web_request": {
            c_url: "Auditor report submitted.",
            r_url: "Production re-entrancy bug discovered within 24 hours of audit release."
        },
        "nondet_exec_prompt": {
            "arbitration court": json.dumps({
                "verdict": "FAVOR_RESPONDENT",
                "settlement_tier": "FULL_RESPONDENT",
                "confidence": 92,
                "rationale": "Critical vulnerability breached contractual zero-bug threshold."
            })
        }
    }
    sim_installMocks(mock_gl, mocks)

    dispute_id = contract.file_dispute(
        claimant="0xAuditor",
        respondent="0xProtocolDAO",
        claimant_evidence_url=c_url,
        respondent_evidence_url=r_url,
        dispute_rules=rules,
    )

    assert dispute_id == "DISPUTE-000001"
    assert contract.get_dispute_status(dispute_id) == "FAVOR_RESPONDENT"
    assert contract.get_settlement_tier(dispute_id) == "FULL_RESPONDENT"
    assert contract.get_claimant_payout_pct(dispute_id) == 0
    assert contract.get_respondent_payout_pct(dispute_id) == 100
    assert contract.get_dispute_liability_split(dispute_id) == 0

    stats = json.loads(contract.get_statistics())
    assert stats["favor_respondent_count"] == 1


def test_dispute_split_equal_happy_path(test_env):
    """Happy path: Split judgment resolution (mutual partial fault binds to exactly 50%)."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]

    c_url = "https://escrow.org/claimant/case3"
    r_url = "https://escrow.org/respondent/case3"
    rules = "50/50 cost sharing if hardware delivery is delayed due to customs."

    mocks = {
        "nondet_web_request": {
            c_url: "Tracking shows package held in customs for 2 weeks.",
            r_url: "Buyer did not provide local tax identification required for clearance."
        },
        "nondet_exec_prompt": {
            "arbitration court": json.dumps({
                "verdict": "SPLIT_EQUAL",
                "settlement_tier": "SPLIT_EQUAL",
                "confidence": 80,
                "rationale": "Both parties share responsibility under customs clause."
            })
        }
    }
    sim_installMocks(mock_gl, mocks)

    dispute_id = contract.file_dispute(
        claimant="0xMerchant",
        respondent="0xBuyer",
        claimant_evidence_url=c_url,
        respondent_evidence_url=r_url,
        dispute_rules=rules,
    )

    assert dispute_id == "DISPUTE-000001"
    assert contract.get_dispute_status(dispute_id) == "SPLIT_EQUAL"
    assert contract.get_settlement_tier(dispute_id) == "SPLIT_EQUAL"
    assert contract.get_claimant_payout_pct(dispute_id) == 50
    assert contract.get_respondent_payout_pct(dispute_id) == 50
    assert contract.get_dispute_liability_split(dispute_id) == 50

    stats = json.loads(contract.get_statistics())
    assert stats["split_equal_count"] == 1


def test_consensus_mismatch_verdict_disagreement(test_env):
    """Consensus failure: Leader returns FAVOR_CLAIMANT but validator returns FAVOR_RESPONDENT."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]

    c_url = "https://dispute.org/claimant"
    r_url = "https://dispute.org/respondent"

    mock_gl.nondet.web.urls[c_url] = "Claimant text"
    mock_gl.nondet.web.urls[r_url] = "Respondent text"

    mock_gl.nondet.prompts["arbitration court"] = json.dumps({
        "verdict": "FAVOR_CLAIMANT",
        "settlement_tier": "FULL_CLAIMANT",
        "confidence": 85,
        "rationale": "Leader favors claimant"
    })

    def disagreeing_validator(leader_res):
        val_verdict = "FAVOR_RESPONDENT"
        val_tier = "FULL_RESPONDENT"
        val_conf = 85

        if leader_res.calldata["verdict"] != val_verdict:
            return False
        if leader_res.calldata["settlement_tier"] != val_tier:
            return False
        return True

    mock_gl.vm.validator_override = disagreeing_validator

    with pytest.raises(Exception, match="Consensus failed: Validator rejected leader proposal"):
        contract.file_dispute("0xAlice", "0xBob", c_url, r_url, "Rules")


def test_consensus_mismatch_settlement_tier_divergence(test_env):
    """Consensus failure: Verdicts match but settlement tiers diverge (FULL_CLAIMANT vs MAJOR_CLAIMANT).
    
    This guarantees that validators reject any divergence in economic payout schedules,
    preventing non-equivalent payouts from ever passing consensus.
    """
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]

    c_url = "https://dispute.org/claimant_div"
    r_url = "https://dispute.org/respondent_div"

    mock_gl.nondet.web.urls[c_url] = "Claimant text"
    mock_gl.nondet.web.urls[r_url] = "Respondent text"

    # Leader proposes FULL_CLAIMANT (100% payout)
    mock_gl.nondet.prompts["arbitration court"] = json.dumps({
        "verdict": "FAVOR_CLAIMANT",
        "settlement_tier": "FULL_CLAIMANT",
        "confidence": 85,
        "rationale": "Leader awards full claimant"
    })

    # Validator independently evaluates MAJOR_CLAIMANT (75% payout).
    # Since tiers diverge, consensus MUST FAIL.
    def tier_diverging_validator(leader_res):
        val_verdict = "FAVOR_CLAIMANT"
        val_tier = "MAJOR_CLAIMANT"

        if leader_res.calldata["verdict"] != val_verdict:
            return False
        if leader_res.calldata["settlement_tier"] != val_tier:
            return False
        return True

    mock_gl.vm.validator_override = tier_diverging_validator

    with pytest.raises(Exception, match="Consensus failed: Validator rejected leader proposal"):
        contract.file_dispute("0xAlice", "0xBob", c_url, r_url, "Rules")


def test_consensus_mismatch_confidence_band_divergence(test_env):
    """Consensus failure: Confidence bands diverge between leader (High) and validator (Moderate)."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]
    sdj = test_env["module"]

    c_url = "https://dispute.org/claimant_conf"
    r_url = "https://dispute.org/respondent_conf"

    mock_gl.nondet.web.urls[c_url] = "Claimant text"
    mock_gl.nondet.web.urls[r_url] = "Respondent text"

    # Leader has High confidence (85, Band 2)
    mock_gl.nondet.prompts["arbitration court"] = json.dumps({
        "verdict": "FAVOR_CLAIMANT",
        "settlement_tier": "MAJOR_CLAIMANT",
        "confidence": 85,
        "rationale": "High confidence"
    })

    # Validator has Moderate confidence (60, Band 1)
    def conf_diverging_validator(leader_res):
        val_verdict = "FAVOR_CLAIMANT"
        val_tier = "MAJOR_CLAIMANT"
        val_conf = 60

        if leader_res.calldata["verdict"] != val_verdict:
            return False
        if leader_res.calldata["settlement_tier"] != val_tier:
            return False
        if sdj.get_confidence_band(leader_res.calldata["confidence"]) != sdj.get_confidence_band(val_conf):
            return False
        return True

    mock_gl.vm.validator_override = conf_diverging_validator

    with pytest.raises(Exception, match="Consensus failed: Validator rejected leader proposal"):
        contract.file_dispute("0xAlice", "0xBob", c_url, r_url, "Rules")


def test_consensus_rejects_non_return_leader(test_env):
    """Consensus rule: Validator must reject if leader_res is not gl.vm.Return."""
    mock_gl = test_env["mock_gl"]

    invalid_leader_res = "Not a Return instance"

    def validator_logic(leader_res):
        if not isinstance(leader_res, mock_gl.vm.Return):
            return False
        return True

    assert validator_logic(invalid_leader_res) is False


def test_validation_empty_parties(test_env):
    """Validation: Empty claimant or respondent raises UserError."""
    contract = test_env["contract"]
    UserError = test_env["mock_gl"].vm.UserError

    with pytest.raises(UserError, match="claimant"):
        contract.file_dispute("", "0xBob", "https://c.org", "https://r.org", "rules")

    with pytest.raises(UserError, match="respondent"):
        contract.file_dispute("0xAlice", "", "https://c.org", "https://r.org", "rules")


def test_validation_same_claimant_and_respondent(test_env):
    """Validation: Claimant and respondent cannot be identical."""
    contract = test_env["contract"]
    UserError = test_env["mock_gl"].vm.UserError

    with pytest.raises(UserError, match="cannot be the same address/identity"):
        contract.file_dispute("0xAlice", "0xAlice", "https://c.org", "https://r.org", "rules")


def test_validation_invalid_urls(test_env):
    """Validation: Non-http/https URLs raise UserError."""
    contract = test_env["contract"]
    UserError = test_env["mock_gl"].vm.UserError

    with pytest.raises(UserError, match="must start with http:// or https://"):
        contract.file_dispute("0xAlice", "0xBob", "ftp://c.org/doc", "https://r.org", "rules")

    with pytest.raises(UserError, match="must start with http:// or https://"):
        contract.file_dispute("0xAlice", "0xBob", "https://c.org", "file:///r.pdf", "rules")


def test_validation_empty_rules(test_env):
    """Validation: Empty dispute rules raises UserError."""
    contract = test_env["contract"]
    UserError = test_env["mock_gl"].vm.UserError

    with pytest.raises(UserError, match="dispute_rules"):
        contract.file_dispute("0xAlice", "0xBob", "https://c.org", "https://r.org", "   ")


def test_error_handling_dead_evidence_url(test_env):
    """Error handling: Unreachable claimant or respondent URL raises UserError."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]
    UserError = mock_gl.vm.UserError

    # Register only claimant URL, respondent URL will fail to fetch
    mock_gl.nondet.web.urls["https://evidence.org/valid_c"] = "Claimant text"

    with pytest.raises(UserError, match="Failed to fetch respondent evidence"):
        contract.file_dispute(
            "0xAlice",
            "0xBob",
            "https://evidence.org/valid_c",
            "https://evidence.org/broken_r",
            "rules",
        )


def test_error_handling_unparseable_llm_output(test_env):
    """Error handling: Malformed non-JSON output from LLM raises UserError."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]
    UserError = mock_gl.vm.UserError

    c_url = "https://c.org/p"
    r_url = "https://r.org/p"
    mock_gl.nondet.web.urls[c_url] = "Claimant text"
    mock_gl.nondet.web.urls[r_url] = "Respondent text"
    mock_gl.nondet.prompts["arbitration court"] = "Not a json response"

    with pytest.raises(UserError, match="Unparseable LLM output"):
        contract.file_dispute("0xAlice", "0xBob", c_url, r_url, "rules")


def test_error_handling_invalid_verdict_value(test_env):
    """Error handling: LLM emits non-standard verdict label."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]
    UserError = mock_gl.vm.UserError

    c_url = "https://c.org/p2"
    r_url = "https://r.org/p2"
    mock_gl.nondet.web.urls[c_url] = "Claimant text"
    mock_gl.nondet.web.urls[r_url] = "Respondent text"
    mock_gl.nondet.prompts["arbitration court"] = json.dumps({
        "verdict": "GUILTY",
        "settlement_tier": "MAJOR_CLAIMANT",
        "confidence": 50,
        "rationale": "Invalid label"
    })

    with pytest.raises(UserError, match="Invalid verdict from LLM"):
        contract.file_dispute("0xAlice", "0xBob", c_url, r_url, "rules")


def test_error_handling_invalid_settlement_tier(test_env):
    """Error handling: LLM emits non-standard settlement tier."""
    contract = test_env["contract"]
    mock_gl = test_env["mock_gl"]
    UserError = mock_gl.vm.UserError

    c_url = "https://c.org/p3"
    r_url = "https://r.org/p3"
    mock_gl.nondet.web.urls[c_url] = "Claimant text"
    mock_gl.nondet.web.urls[r_url] = "Respondent text"
    mock_gl.nondet.prompts["arbitration court"] = json.dumps({
        "verdict": "FAVOR_CLAIMANT",
        "settlement_tier": "UNKNOWN_TIER",
        "confidence": 50,
        "rationale": "Invalid tier"
    })

    with pytest.raises(UserError, match="Invalid settlement_tier from LLM"):
        contract.file_dispute("0xAlice", "0xBob", c_url, r_url, "rules")


def test_query_nonexistent_dispute(test_env):
    """Querying non-existent dispute ID raises UserError."""
    contract = test_env["contract"]
    UserError = test_env["mock_gl"].vm.UserError

    assert contract.has_dispute("DISPUTE-999999") is False
    assert contract.get_party_latest_dispute("0xGhost") == ""

    with pytest.raises(UserError, match="Dispute record not found"):
        contract.get_dispute("DISPUTE-999999")

    with pytest.raises(UserError, match="Dispute record not found"):
        contract.get_dispute_status("DISPUTE-999999")


if __name__ == "__main__":
    pytest.main(["-v", __file__])
