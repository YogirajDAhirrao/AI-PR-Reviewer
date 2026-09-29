"""
    python main.py <owner> <repo> <pr_number>

Example:
    python main.py octocat hello-world 42
"""
import asyncio
import sys

from dotenv import load_dotenv

from agent import review_pr

load_dotenv()


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    owner, repo, pr_number = sys.argv[1], sys.argv[2], int(sys.argv[3])
    result = asyncio.run(review_pr(owner, repo, pr_number))
    print("\n" + "=" * 60)
    print(result)


if __name__ == "__main__":
    main()
