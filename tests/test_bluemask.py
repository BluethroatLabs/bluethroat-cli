from pathlib import Path

from PIL import Image, PngImagePlugin

from bluethroat.bluemask import FILL, mask_file
from bluethroat.cli import main
from bluethroat.errors import INVALID


def test_secure_mask_uses_two_corners_and_writes_fresh_png(tmp_path: Path, capsys) -> None:
    source = tmp_path / "private.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Secret", "private metadata")
    Image.new("RGBA", (6, 5), (255, 0, 0, 128)).save(source, pnginfo=metadata)

    assert main(["bluemask", "blur", str(source), "5,4", "1,1"]) == 0

    output = tmp_path / "private-bluemask.png"
    captured = capsys.readouterr()
    assert f"Wrote: {output}" in captured.out
    assert "secure solid mask" in captured.out
    with Image.open(output) as image:
        assert image.mode == "RGB"
        assert image.getpixel((1, 1)) == FILL
        assert image.getpixel((4, 3)) == FILL
        assert image.getpixel((5, 4)) == (255, 127, 127)
        assert "Secret" not in image.info


def test_cosmetic_blur_changes_only_the_selected_rectangle(tmp_path: Path, capsys) -> None:
    source = tmp_path / "split.png"
    image = Image.new("RGB", (10, 5), "red")
    for x in range(5, 10):
        for y in range(5):
            image.putpixel((x, y), (0, 0, 255))
    image.save(source)

    output = mask_file(source, "1,0", "9,5", cosmetic=True)

    with Image.open(output) as result:
        assert result.getpixel((0, 2)) == (255, 0, 0)
        assert result.getpixel((9, 2)) == (0, 0, 255)
        assert result.getpixel((4, 2)) not in {(255, 0, 0), (0, 0, 255), FILL}

    forced = tmp_path / "forced.png"
    assert (
        main(
            [
                "bluemask",
                "blur",
                str(source),
                "1,0",
                "9,5",
                "--cosmetic",
                "-o",
                str(forced),
            ]
        )
        == 0
    )
    assert "appearance only" in capsys.readouterr().out


def test_coordinates_follow_exif_oriented_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (3, 2), "green").save(source, exif=exif)

    output = mask_file(source, "0,0", "2,3")

    with Image.open(output) as result:
        assert result.size == (2, 3)
        assert {
            result.getpixel((x, y))
            for x in range(result.width)
            for y in range(result.height)
        } == {FILL}


def test_coordinates_and_output_are_validated_before_writing(tmp_path: Path, capsys) -> None:
    source = tmp_path / "image.png"
    Image.new("RGB", (8, 6), "white").save(source)

    assert main(["bluemask", "blur", str(source), "9,0", "4,4"]) == INVALID
    assert "within 0,0 and 8,6" in capsys.readouterr().err

    assert main(["bluemask", "blur", str(source), "2,2", "2,5"]) == INVALID
    assert "non-empty rectangle" in capsys.readouterr().err

    existing = tmp_path / "existing.png"
    existing.write_bytes(b"keep me")
    assert (
        main(
            [
                "bluemask",
                "blur",
                str(source),
                "0,0",
                "1,1",
                "-o",
                str(existing),
            ]
        )
        == INVALID
    )
    assert existing.read_bytes() == b"keep me"


def test_missing_bluemask_subcommand_is_invalid(capsys) -> None:
    assert main(["bluemask"]) == INVALID
    assert "bluethroat bluemask blur" in capsys.readouterr().err
