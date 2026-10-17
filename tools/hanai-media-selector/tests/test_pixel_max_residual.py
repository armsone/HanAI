import importlib.util
import os
import tempfile
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rpa", os.path.join(HERE, "..", "reference_pixel_agreement.py"))
rpa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rpa)

try:
    import numpy
    from PIL import Image
except Exception:
    numpy = None


@unittest.skipIf(numpy is None, "Pillow/numpy not available")
class MaxResidualTest(unittest.TestCase):
    def test_max_catches_localized_patch_where_mad_is_low(self):
        rng = numpy.random.RandomState(0)
        base = numpy.clip(128 + rng.randn(256, 256, 3) * 20, 0, 255).astype("uint8")
        changed = base.copy()
        changed[100:108, 100:108] = 255 - changed[100:108, 100:108]
        with tempfile.TemporaryDirectory() as d:
            pa, pb = os.path.join(d, "a.png"), os.path.join(d, "b.png")
            Image.fromarray(base).save(pa)
            Image.fromarray(changed).save(pb)
            r = rpa.compare(rpa.load_rgb(pa), rpa.load_rgb(pb))
            same = rpa.compare(rpa.load_rgb(pa), rpa.load_rgb(pa))
        m = r["metrics"]
        self.assertLess(m["rgbMad"], 0.02)
        self.assertGreater(m["maxAbsoluteResidual"], 0.3)
        self.assertEqual(same["metrics"]["maxAbsoluteResidual"], 0.0)

    def test_extreme_aspect_raises_fixed_error_without_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            for size in ((1, 16384), (16384, 1)):
                p = os.path.join(d, "x%d.png" % size[0])
                Image.new("RGB", size, (10, 20, 30)).save(p)
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    with self.assertRaises(rpa.PixelError) as cm:
                        rpa.compare(rpa.load_rgb(p), rpa.load_rgb(p))
                self.assertEqual(str(cm.exception), "pixel_aspect_extreme")

    def test_normal_image_still_computes_without_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.png")
            Image.new("RGB", (64, 48), (10, 20, 30)).save(p)
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                r = rpa.compare(rpa.load_rgb(p), rpa.load_rgb(p))
        self.assertEqual(r["status"], "computed")


if __name__ == "__main__":
    unittest.main()
