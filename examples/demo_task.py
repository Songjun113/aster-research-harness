"""Copy explicitly synthetic probabilities into the task's review output."""
import shutil
import sys

shutil.copyfile(sys.argv[1], sys.argv[2])
