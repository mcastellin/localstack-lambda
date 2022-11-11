from setuptools import setup, find_packages

setup(
    name="localstack-lambda",
    version="0.1.1",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "click",
        "boto3",
        "pyyaml",
        "docker",
    ],
    entry_points={
        "console_scripts": [
            "lambdalocal=lambdalocal.main:cli",
        ],
    },
)
