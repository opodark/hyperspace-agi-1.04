import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def functions(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


class DreamRouteSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = functions(ROOT / "node" / "main.py")
        cls.control_plane = functions(ROOT / "control-plane" / "main.py")
        cls.gateway_source = (ROOT / "federation-gateway" / "main.py").read_text(encoding="utf-8")
        cls.dashboard = (ROOT / "control-plane" / "dashboard.html").read_text(encoding="utf-8")

    def test_node_review_requires_configured_matching_token(self):
        source = ast.unparse(self.node["review_dream"])
        self.assertIn("len(DREAM_REVIEW_TOKEN) < 32", source)
        self.assertIn("token_authorized(provided, DREAM_REVIEW_TOKEN)", source)

    def test_control_plane_checks_browser_token_and_replaces_it_for_node(self):
        review_source = ast.unparse(self.control_plane["dream_node_review"])
        auth_source = ast.unparse(self.control_plane["_dream_review_auth_error"])
        self.assertIn("_dream_review_auth_error", review_source)
        self.assertIn("Bearer {DREAM_REVIEW_TOKEN}", review_source)
        self.assertIn("token_authorized(provided, DREAM_REVIEW_TOKEN)", auth_source)

    def test_dream_routes_are_not_exposed_by_public_federation_gateway(self):
        tree = ast.parse(self.gateway_source)
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "ALLOWED_ROUTES"
                                  for target in node.targets))
        self.assertNotIn("/dreams", ast.unparse(assignment))

    def test_dashboard_uses_password_field_and_authorization_header(self):
        self.assertIn('id="dreamReviewToken"', self.dashboard)
        self.assertIn("'Authorization':'Bearer '+token", self.dashboard)


if __name__ == "__main__":
    unittest.main()
