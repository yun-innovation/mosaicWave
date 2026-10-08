"""Rasterize src/brand/mosaicWave-icon.png into QPKG, web, Windows, and macOS sizes."""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "src" / "brand" / "mosaicWave-icon.png"


def fit(im: Image.Image, size: int) -> Image.Image:
    out = im.convert("RGBA")
    return out.resize((size, size), Image.Resampling.LANCZOS)


def gray64(im: Image.Image) -> Image.Image:
    g = ImageOps.autocontrast(ImageOps.grayscale(fit(im, 64)))
    return Image.merge("RGB", (g, g, g))


def main() -> None:
    if not MASTER.is_file():
        raise SystemExit(f"missing master icon: {MASTER}")
    src = Image.open(MASTER)
    web_public = ROOT / "src" / "web" / "public"
    web_app = ROOT / "src" / "web" / "app"
    qpkg = ROOT / "src" / "qpkg" / "icons"
    msi = ROOT / "src" / "msi" / "shared"
    web_public.mkdir(parents=True, exist_ok=True)
    web_app.mkdir(parents=True, exist_ok=True)
    qpkg.mkdir(parents=True, exist_ok=True)
    msi.mkdir(parents=True, exist_ok=True)

    shutil.copy2(MASTER, web_public / "mosaicWave-icon.png")
    fit(src, 180).save(web_public / "apple-touch-icon.png", "PNG")
    fit(src, 192).save(web_app / "icon.png", "PNG")
    fit(src, 180).save(web_app / "apple-icon.png", "PNG")

    ico_sizes = [(16, 16), (32, 32), (48, 48), (256, 256)]
    fit(src, 256).save(web_app / "favicon.ico", format="ICO", sizes=ico_sizes)
    fit(src, 256).save(msi / "mosaicWave.ico", format="ICO", sizes=ico_sizes)

    posix = ROOT / "src" / "posix"
    posix.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MASTER, posix / "mosaicWave-icon.png")
    icns_sizes = (32, 64, 128, 256, 512, 1024)
    icns_images = [fit(src, size).convert("RGBA") for size in icns_sizes]
    icns_images[-1].save(
        posix / "mosaicWave.icns",
        format="ICNS",
        append_images=icns_images[:-1],
    )

    fit(src, 64).convert("RGB").save(qpkg / "mosaicWave.png", "PNG")
    fit(src, 80).convert("RGB").save(qpkg / "mosaicWave_80.png", "PNG")
    gray64(src).save(qpkg / "mosaicWave_gray.png", "PNG")
    print("exported icons from", MASTER)


if __name__ == "__main__":
    main()
