import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager

class LocalNodeTests(unittest.TestCase):
    def test_old_synthetic_nodes_are_archived_without_losing_last_seen(self):
        tree=ast.parse((Path(__file__).parents[1]/"control-plane/main.py").read_text(encoding="utf-8"))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_load_nodes_from_db")
        rows=[{"node_id":"local-old","endpoint":"","status":"active","last_seen":"old time"},
              {"node_id":"local-current","endpoint":"","status":"active"},
              {"node_id":"mac","endpoint":"http://mac:8081","status":"active"}]
        calls=[]
        @contextmanager
        def connection():
            yield SimpleNamespace(execute=lambda sql,args:calls.append((sql,args)))
        scope={"db":SimpleNamespace(get_all_nodes=lambda:rows,_conn=connection),
               "_normalize_endpoint":lambda x:x,"_LOCAL_NODE_ID":"local-current",
               "_nodes_by_id":{},"_known_endpoints":set(),"NODE_ENDPOINTS":[]}
        exec(compile(ast.Module(body=[function],type_ignores=[]),"nodes","exec"),scope)
        scope["_load_nodes_from_db"]()
        self.assertEqual(scope["_nodes_by_id"]["local-old"]["status"],"unreachable")
        self.assertEqual(scope["_nodes_by_id"]["local-old"]["last_seen"],"old time")
        self.assertEqual(scope["_nodes_by_id"]["mac"]["status"],"active")
        self.assertEqual(calls[0][1],("local-old",))
