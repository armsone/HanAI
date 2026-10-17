import importlib.util, pathlib, io, json, os, tempfile, unittest, contextlib
from unittest import mock

SRC = str(pathlib.Path(__file__).resolve().parents[1] / "analyze_media.py")
spec = importlib.util.spec_from_file_location("am", SRC)
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)


def run(argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = am.main(argv)
        except SystemExit as e:
            code = e.code
    text = out.getvalue() or err.getvalue()
    return code, json.loads(text.strip().splitlines()[-1])


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.files = []
        for n in "rabc":
            p = os.path.join(self.d.name, n + ".jpg")
            open(p, "wb").write(n.encode())
            self.files.append(p)
        self.ref, *self.cands = self.files
        self.tiles = {b"r": 0.0, b"a": 0.5, b"b": 0.2, b"c": 0.2}
        self.engine_inputs = []
        self.fail_decode = set()

        def decode(ffmpeg, path, rot, *a, **k):
            return open(path, "rb").read()

        def desc(raw, box):
            if raw in self.fail_decode:
                raise am.SanitizedError("decode_failed")
            return 1.0, 1.0, self.tiles[raw], raw

        def dist(a, b):
            return abs(a[0] - b[0])

        patches = [
            mock.patch.object(am, "find_tool", lambda n, o: n),
            mock.patch.object(am, "probe_image", lambda f, p: (10, 10, 0)),
            mock.patch.object(am, "decode_canvas", decode),
            mock.patch.object(am, "content_box", lambda *a: None),
            mock.patch.object(am, "compute_descriptor", desc),
            mock.patch.object(am, "descriptor_distance", dist),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def args(self, *extra, cands=None):
        a = ["images"]
        for c in cands or self.cands:
            a += ["--image", c]
        return a + list(extra)

    def test_matches_ties_and_schema(self):
        code, r = run(self.args("--reference-image", self.ref))
        self.assertEqual(code, 0)
        self.assertEqual([m["index"] for m in r["referenceMatches"]], [1, 2, 0])
        self.assertTrue(r["reference"]["reviewOnly"])
        self.assertEqual(r["reference"]["availableCandidateCount"], 3)
        self.assertEqual(r["reference"]["requestedLimit"], 20)
        self.assertNotIn(self.ref, json.dumps(r))
        self.assertEqual(len(r["candidates"]), 3)

    def test_limit(self):
        _, r = run(self.args("--reference-image", self.ref, "--reference-limit", "1"))
        self.assertEqual(len(r["referenceMatches"]), 1)
        self.assertEqual(r["reference"]["availableCandidateCount"], 3)

    def test_argument_errors(self):
        for extra in (["--reference-limit", "3"],
                      ["--reference-image", self.ref, "--reference-limit", "0"],
                      ["--reference-image", self.ref, "--reference-limit", "201"],
                      ["--reference-image", self.ref, "--reference-limit", "-1"],
                      ["--reference-image", self.ref, "--query", "x", "--embedding-backend", "siglip2", "--model-dir", "m"]):
            code, r = run(self.args(*extra))
            self.assertNotEqual(code, 0)
            self.assertEqual(r["status"], "error")
        code, _ = run(self.args("--reference-image", self.ref, "--reference-limit", "nan"))
        self.assertNotEqual(code, 0)

    def test_invalid_reference_before_candidates(self):
        with mock.patch.object(am, "decode_canvas", side_effect=AssertionError):
            code, r = run(self.args("--reference-image", os.path.join(self.d.name, "nope.jpg")))
        self.assertNotEqual(code, 0)
        self.assertEqual(r["error"], "reference_invalid")

    def test_reference_decode_failure(self):
        self.fail_decode.add(b"r")
        code, r = run(self.args("--reference-image", self.ref))
        self.assertNotEqual(code, 0)
        self.assertEqual(r["error"], "reference_decode_failed")
        self.assertNotIn("referenceMatches", r)

    def test_partial_candidate_failure(self):
        self.fail_decode.add(b"a")
        code, r = run(self.args("--reference-image", self.ref))
        self.assertEqual(code, 0)
        self.assertEqual(r["status"], "partial")
        self.assertEqual([m["index"] for m in r["referenceMatches"]], [1, 2])

    def test_no_input_change(self):
        _, base = run(self.args())
        _, r = run(self.args("--reference-image", self.ref))
        for k in ("candidates", "distances", "counts", "status"):
            self.assertEqual(base[k], r[k])
        self.assertNotIn("referenceMatches", base)

    def test_ast(self):
        import ast
        ast.parse(open(SRC).read())


class FakeHelper:
    CONFIG = {"id": "fake"}

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.loads = []

    def load_rgb(self, path):
        self.loads.append(path)
        if os.path.basename(path) in self.fail:
            raise RuntimeError("secret " + path)
        return path

    def compare(self, ref, cand):
        if os.path.basename(cand) == "b.jpg":
            return {"status": "aspect_mismatch", "metrics": None}
        return {"status": "computed", "metrics": {"ssimGlobal": 0.5, "tileSsimMin": 0.4, "rgbMad": 0.1}}


class VerifyTests(T):
    def setUp(self):
        super().setUp()
        vec = {b"r": [0.0], b"a": [0.5], b"b": [0.2], b"c": [0.2]}
        self.helper = FakeHelper()
        patches = [
            mock.patch.object(am, "init_learned", lambda a: (object(), "dino-ns")),
            mock.patch.object(am, "content_dims", lambda *a: (4, 4)),
            mock.patch.object(am, "decode_exact", lambda f, p, r, w, h: open(p, "rb").read()),
            mock.patch.object(am, "encode_with_learned", lambda l, raw, w, h: (vec[raw], None)),
            mock.patch.object(am, "embedding_distance", lambda a, b: abs(a[0] - b[0])),
            mock.patch.object(am, "build_embedding_distances", lambda v: []),
            mock.patch.object(am, "load_pixel_helper", lambda: self.helper),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def dino(self, *extra):
        return self.args("--embedding-backend", "dinov2", "--model-dir", "m", "--reference-image", self.ref, *extra)

    def test_requires_flag_combinations(self):
        for argv, err in ((self.args("--verify-reference-copies"), "verify_requires_reference"),
                          (self.args("--reference-image", self.ref, "--verify-reference-copies"), "verify_requires_dinov2"),
                          (self.args("--embedding-backend", "siglip2", "--model-dir", "m", "--reference-image", self.ref,
                                     "--verify-reference-copies"), "verify_requires_dinov2")):
            code, r = run(argv)
            self.assertNotEqual(code, 0)
            self.assertEqual(r["error"], err)

    def test_metrics_only_on_returned_matches(self):
        code, r = run(self.dino("--verify-reference-copies", "--reference-limit", "2"))
        self.assertEqual(code, 0)
        self.assertEqual(len(r["referenceMatches"]), 2)
        self.assertEqual(len(self.helper.loads), 3)  # reference + 2 returned candidates
        self.assertTrue(r["reference"]["reviewOnly"])
        by = {m["index"]: m["pixelAgreement"] for m in r["referenceMatches"]}
        self.assertEqual(by[1]["status"], "aspect_mismatch")  # b.jpg
        self.assertEqual(by[2]["status"], "computed")
        self.assertIn("rgbMad", by[2]["metrics"])
        for m in r["referenceMatches"]:
            self.assertNotIn("copyEvidence", m)

    def test_candidate_pixel_failure_is_unknown(self):
        self.helper.fail.add(os.path.basename(self.cands[1]))
        code, r = run(self.dino("--verify-reference-copies"))
        self.assertEqual(code, 0)
        bad = [m for m in r["referenceMatches"] if m["index"] == 1][0]
        self.assertEqual(bad["pixelAgreement"], {"status": "unknown", "metrics": None})
        self.assertNotIn("secret", json.dumps(r))

    def test_reference_pixel_failure(self):
        self.helper.fail.add(os.path.basename(self.ref))
        code, r = run(self.dino("--verify-reference-copies"))
        self.assertNotEqual(code, 0)
        self.assertEqual(r["error"], "reference_pixel_failed")

    def test_default_unchanged(self):
        _, base = run(self.dino())
        _, ver = run(self.dino("--verify-reference-copies"))
        self.assertEqual(self.helper.loads.count(self.ref), 1)
        for k in ("candidates", "distances", "counts", "status"):
            self.assertEqual(base[k], ver[k])
        self.assertEqual([(m["index"], m["distance"]) for m in base["referenceMatches"]],
                         [(m["index"], m["distance"]) for m in ver["referenceMatches"]])
        self.assertTrue(all("pixelAgreement" not in m for m in base["referenceMatches"]))
        self.assertNotIn("pixelAgreement", base["reference"])


if __name__ == "__main__":
    unittest.main()
