
import os

version_file = "version.txt"
if os.path.exists(version_file):
    with open(version_file, "r") as f:
        val = int(f.read().strip() or "1")
else:
    val = 1

val += 1
with open(version_file, "w") as f:
    f.write(str(val))

print(f"Updated deployment version to: {val}")
