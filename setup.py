from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="jianpian-downloader",
    version="1.0.3",
    author="skyfireitdiy",
    author_email="skyfireitdiy@hotmail.com",
    description="一个优雅的视频下载工具",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/skyfireitdiy/JianpianDownloader",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Environment :: Console",
        "Topic :: Internet :: WWW/HTTP :: Browsers",
        "Topic :: Multimedia :: Video",
    ],
    python_requires=">=3.7",
    install_requires=[
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "m3u8>=3.6.0",
        "rich>=13.7.0",
        "tqdm>=4.66.0",
    ],
    entry_points={
        "console_scripts": [
            "jianpian-dl=jianpian_downloader.movie_downloader:main",
        ],
    },
)
