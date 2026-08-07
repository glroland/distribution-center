# OpenShift AI 3.4 EvalHub Example

## 1 The Scenario Dataset (dataset.json)
This dataset contains explicit trajectories. It tells EvalHub which MCP tool should have been called, preventing scenarios where the agent hallucinates a correct-looking text answer using completely wrong tools.

[
  {
    "id": "case_01",
    "user_query": "Check system logs for memory warnings and create a Jira ticket with the logs attached.",
    "expected_mcp_sequence": ["log_fetcher::get_warnings", "jira_client::create_ticket"],
    "ground_truth_keywords": ["PROD-1029", "Memory threshold exceeded"]
  },
  {
    "id": "case_02",
    "user_query": "Optimize my local database index without querying cloud systems.",
    "expected_mcp_sequence": ["db_tuner::analyze_index"],
    "ground_truth_keywords": ["INDEX CREATED", "Query plan optimized"]
  }
]


## 2 The EvalHub Adapter (adapter.py)
This script uses EvalHub's internal mechanisms to load your dataset, simulate an agent run by invoking your multi-agent architecture, capture execution metrics, and export them directly to the embedded MLflow control plane.

import os
import json
import asyncio
from evalhub_sdk.v1 import FrameworkAdapter, EvalResult
# Mocking an imaginary orchestrator framework for this example
from complex_a2a_agent import MultiMCPOrchestrator

class MultiAgentMcpAdapter(FrameworkAdapter):
    """
    Custom RHOAI EvalHub Adapter for multi-turn Agent-to-Agent
    systems routed across various MCP endpoints.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize your agent architecture with mock/test MCP server configurations
        self.orchestrator = MultiMCPOrchestrator(
            mcp_manifest=os.getenv("TEST_MCP_CONFIG", "/etc/mcp/test_config.json")
        )

    async def execute_test_matrix(self, dataset_path: str) -> EvalResult:
        # 1. Read input scenarios from OpenShift storage volume
        with open(dataset_path, 'r') as f:
            test_cases = json.load(f)
            
        metrics_summary = {
            "total_runs": len(test_cases),
            "correct_mcp_routing_count": 0,
            "infinite_loops_detected": 0,
            "accuracy_score": 0.0
        }
        
        runs_telemetry = []

        # 2. Iterate through test trajectories
        for case in test_cases:
            user_input = case["user_query"]
            expected_route = case["expected_mcp_sequence"]
            
            # Execute your system and capture tool invocation trace logs
            agent_response, execution_trace = await self.orchestrator.run(user_input)
            
            # Extract actual tool calls from execution logs
            # e.g., ["log_fetcher::get_warnings", "jira_client::create_ticket"]
            actual_route = execution_trace.get_invoked_mcp_tools() 
            is_looping = execution_trace.metadata.get("loop_detected", False)
            
            # 3. Compute Metrics
            routing_match = (actual_route == expected_route)
            if routing_match:
                metrics_summary["correct_mcp_routing_count"] += 1
            if is_looping:
                metrics_summary["infinite_loops_detected"] += 1
                
            runs_telemetry.append({
                "case_id": case["id"],
                "success": routing_match and not is_looping,
                "actual_sequence": actual_route,
                "expected_sequence": expected_route
            })

        # Calculate absolute benchmark baseline
        metrics_summary["accuracy_score"] = metrics_summary["correct_mcp_routing_count"] / metrics_summary["total_runs"]

        # 4. Return results structured for EvalHub / MLflow Dashboard
        return EvalResult(
            metrics=metrics_summary,
            artifacts={"detailed_traces.json": json.dumps(runs_telemetry, indent=2)}
        )

if __name__ == "__main__":
    # EvalHub triggers the runner via standardized CLI execution inside the Job container
    adapter = MultiAgentMcpAdapter()
    adapter.start_eval_harness()


## 3 The EvalHub Custom Resource (evaljob.yaml)
Once your image is ready, you tell the EvalHub Server plane to run it by submitting a Kubernetes custom manifest. EvalHub processes this, claims cluster GPUs/CPUs, handles parallel jobs, and reports back.

apiVersion: evalhub.openshift.io/v1alpha1
kind: EvaluationJob
metadata:
  name: a2a-mcp-orchestration-eval
  namespace: rhoai-model-evals
spec:
  # Reference your custom framework adapter image
  frameworkRef:
    image: quay.io/my-org/a2a-mcp-eval-adapter:v1.0.0
  # Dataset location mapped into the runner pod
  dataset:
    configMapRef:
      name: mcp-agent-test-scenarios
      key: dataset.json
  # Compute targets for execution
  resources:
    requests:
      cpu: "2"
      memory: "4Gi"
    limits:
      cpu: "4"
      memory: "8Gi"
  # Targets your existing MLflow integration inside OpenShift AI
  trackingServer:
    mlflowSecretRef:
      name: rhoai-mlflow-connection-profile
