#!/usr/bin/env python3
import os
import re
import sys
import subprocess
from pathlib import Path

def get_version():
    """从 __init__.py 中获取当前版本号"""
    init_file = Path("jianpian_downloader/__init__.py").read_text()
    version_match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_file)
    if not version_match:
        raise RuntimeError("无法找到版本号")
    return version_match.group(1)

def update_version(new_version):
    """更新所有相关文件中的版本号"""
    # 更新 __init__.py
    init_path = Path("jianpian_downloader/__init__.py")
    content = init_path.read_text()
    content = re.sub(
        r'__version__\s*=\s*["\']([^"\']+)["\']',
        f'__version__ = "{new_version}"',
        content
    )
    init_path.write_text(content)
    
    # 更新 setup.py
    setup_path = Path("setup.py")
    content = setup_path.read_text()
    content = re.sub(
        r'version="[^"]+"',
        f'version="{new_version}"',
        content
    )
    setup_path.write_text(content)

def clean_build():
    """清理构建文件"""
    dirs_to_clean = [
        "build",
        "dist",
        "jianpian_downloader.egg-info"
    ]
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            print(f"清理 {dir_name}")
            subprocess.run(["rm", "-rf", dir_name], check=True)

def build_package():
    """构建包"""
    print("构建包...")
    subprocess.run(["python", "-m", "build"], check=True)

def upload_to_pypi():
    """上传到 PyPI"""
    print("上传到 PyPI...")
    subprocess.run(["python", "-m", "twine", "upload", "dist/*"], check=True)

def main():
    current_version = get_version()
    print(f"当前版本: {current_version}")
    
    # 解析版本号
    major, minor, patch = map(int, current_version.split('.'))
    
    print("\n选择要更新的版本类型:")
    print("1. 主版本 (major)")
    print("2. 次版本 (minor)")
    print("3. 补丁版本 (patch)")
    print("4. 使用当前版本")
    print("5. 退出")
    
    choice = input("\n请选择 (1-5): ")
    
    if choice == "1":
        new_version = f"{major + 1}.0.0"
    elif choice == "2":
        new_version = f"{major}.{minor + 1}.0"
    elif choice == "3":
        new_version = f"{major}.{minor}.{patch + 1}"
    elif choice == "4":
        new_version = current_version
    elif choice == "5":
        print("已取消")
        return
    else:
        print("无效的选择")
        return
    
    print(f"\n将版本从 {current_version} 更新到 {new_version}")
    confirm = input("确认继续？(y/N): ")
    if confirm.lower() != 'y':
        print("已取消")
        return
    
    try:
        # 更新版本号
        if new_version != current_version:
            update_version(new_version)
            print(f"版本已更新到 {new_version}")
        
        # 清理旧的构建文件
        clean_build()
        
        # 构建包
        build_package()
        
        # 询问是否上传到 PyPI
        upload = input("\n是否上传到 PyPI？(y/N): ")
        if upload.lower() == 'y':
            upload_to_pypi()
            print("\n✨ 发布完成!")
        else:
            print("\n✨ 构建完成! (未上传)")
            
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main() 