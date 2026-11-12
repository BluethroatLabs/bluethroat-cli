---
name: bluemask
description: Masks rectangular regions in local PNG, JPEG, and WebP images with `bluethroat bluemask`. Use when an agent needs to redact part of an image securely or apply an explicitly requested cosmetic Gaussian blur without uploading the image.
---

# BlueMask

Use `bluethroat bluemask blur` to process an image locally. It does not require login
and always writes a fresh, opaque PNG without source metadata.

## Coordinates

Pass the source image and two `X,Y` corner coordinates:

```bash
bluethroat bluemask blur INPUT.png 40,20 180,70
```

Coordinates refer to the displayed image after EXIF orientation is applied:

- `(0,0)` is the top-left.
- X increases to the right and Y increases downward.
- The right and bottom edges are exclusive. `0,0 100,50` covers pixels
  `x=0..99`, `y=0..49`.
- Either corner order works, but both coordinates must be inside the image bounds.

Inspect the image dimensions and content before choosing coordinates. Include enough
padding to cover antialiasing, shadows, and the complete sensitive value. After writing,
open the output and verify the region visually before sharing it.

## Secure masking

Secure masking is the default. It replaces the selected pixels with opaque `#6e7176`:

```bash
bluethroat bluemask blur screenshot.png 312,148 684,196 \
  -o screenshot-public.png
```

Use this mode for secrets, identifiers, faces, addresses, or any other private content.

## Cosmetic blur

Only pass `--cosmetic` when the user explicitly wants an appearance-only Gaussian blur:

```bash
bluethroat bluemask blur photo.jpg 120,80 360,300 \
  --cosmetic -o photo-blurred.png
```

Cosmetic blur can retain recoverable information. Never describe it as secure redaction
or choose it for sensitive text when the user has not explicitly requested it.

## Output safety

Without `-o`, output is `<source stem>-bluemask.png`; the source is never modified.
Existing outputs, symlinks, source/output aliasing, and non-PNG output names are refused.
Do not pass `--force` unless replacing the named regular output file was explicitly
requested.

Exit code `0` means success. Exit code `4` means invalid coordinates, image, size, format,
or output path. BlueMask accepts PNG, JPEG, and WebP images up to 35 MB, 24 megapixels,
and 12,000 pixels per side.
