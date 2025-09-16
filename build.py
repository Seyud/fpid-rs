#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fpid 编译脚本
自动执行 Rust 项目的编译工作（含格式与静态检查，可跨平台/交叉编译）
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
_USE_TOMLLIB = False
try:
    import tomllib as _toml_lib  # Python 3.11+
    _USE_TOMLLIB = True
except Exception:
    import toml as _toml_lib  # type: ignore  # third-party fallback
    _USE_TOMLLIB = False

def _toml_load_path(path: str):
    if _USE_TOMLLIB:
        with open(path, 'rb') as f:
            return _toml_lib.load(f)  # type: ignore
    else:
        with open(path, 'r', encoding='utf-8') as f:
            return _toml_lib.load(f)  # type: ignore



class GPUGovernorBuilder:
    def __init__(self, config_path="build_config.toml"):
        # 读取配置文件
        config = _toml_load_path(config_path)

        # 配置路径
        paths = config.get('paths', {})
        self.android_ndk_home = paths.get('android_ndk_home')
        self.llvm_path = paths.get('llvm_path')
        # Android API 级别（用于选择 NDK clang 前端 aarch64-linux-android<api>-clang.cmd）
        # 常见可用值：21(最小, 64位从21开始), 24, 26, 28, 30, 33, 34
        self.android_api_level = int(paths.get('android_api_level', 33))

        # 项目配置
        self.target = paths.get('target', "x86_64-pc-windows-msvc")
        self.binary_name = paths.get('binary_name', "fpid")
        self.output_dir = paths.get('output_dir', "output")

        # 判断是否为本地 native 构建：当 target 看起来像主机三元组，且未显式提供 NDK/LLVM 时，放宽校验
        self.native_build = (
            self.target.endswith("windows-msvc") or self.target.endswith("windows-gnu") or self.target.endswith("unknown-linux-gnu")
        ) and not (
            self.android_ndk_home
            and self.llvm_path
            and os.path.exists(self.android_ndk_home)
            and os.path.exists(self.llvm_path)
        )

        # 非 native 构建（例如 Android 交叉编译）需要完整路径
        if not self.native_build:
            if not self.android_ndk_home:
                raise ValueError("配置文件中缺少 android_ndk_home 路径配置")
            if not self.llvm_path:
                raise ValueError("配置文件中缺少 llvm_path 路径配置")

        # 设置环境变量
        self._setup_environment()

    def _is_windows_target(self) -> bool:
        return 'windows' in (self.target or '')

    def _binary_suffix(self) -> str:
        # 根据目标三元组判断后缀，而不是宿主 OS
        return ".exe" if self._is_windows_target() else ""

    def _setup_environment(self):
        """设置编译所需的环境变量"""
        env_vars = {}
        if not self.native_build:
            # 针对 aarch64-linux-android 的交叉编译工具链
            prebuilt = f"{self.android_ndk_home}/toolchains/llvm/prebuilt/windows-x86_64/bin"
            linker = f"{prebuilt}/aarch64-linux-android{self.android_api_level}-clang.cmd"
            # 有些 NDK 版本是 .bat 而非 .cmd，做个兜底
            if not os.path.exists(linker):
                alt = linker[:-4] + ".bat"
                if os.path.exists(alt):
                    linker = alt
            env_vars = {
                "ANDROID_NDK_HOME": self.android_ndk_home,
                "LLVM_PATH": self.llvm_path,
                # 告诉 cargo 使用 NDK 的 clang 作为链接器
                "CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER": linker,
                # 可选：静态库打包器（某些 crate 需要）
                "CARGO_TARGET_AARCH64_LINUX_ANDROID_AR": f"{self.llvm_path}/bin/llvm-ar.exe",
                # bindgen/clang 相关
                "LIBCLANG_PATH": f"{self.llvm_path}/bin",
                "BINDGEN_EXTRA_CLANG_ARGS": f"--target=aarch64-linux-android -I{self.android_ndk_home}/toolchains/llvm/prebuilt/windows-x86_64/sysroot/usr/include",
            }

        # 更新PATH环境变量
        current_path = os.environ.get("PATH", "")
        if not self.native_build:
            new_path_parts = [
                f"{self.llvm_path}/bin",
                f"{self.android_ndk_home}/toolchains/llvm/prebuilt/windows-x86_64/bin",
                current_path,
            ]
            env_vars["PATH"] = ";".join(new_path_parts)

        # 设置环境变量
        for key, value in env_vars.items():
            os.environ[key] = str(value)
            print(f"设置环境变量: {key}={value}")

    def _check_dependencies(self):
        """检查编译依赖是否存在"""
        if self.native_build:
            dependencies = []
        else:
            dependencies = [
                (self.android_ndk_home, "Android NDK"),
                (self.llvm_path, "LLVM"),
            ]

        missing_deps = []
        for path, name in dependencies:
            if not os.path.exists(path):
                missing_deps.append(f"{name}: {path}")

        if missing_deps:
            print("错误：以下依赖项未找到：")
            for dep in missing_deps:
                print(f"  - {dep}")
            return False

        print("所有依赖项检查通过")
        return True

    def build(self):
        """执行Rust项目编译"""
        print("开始编译Rust项目...")

        # 检查依赖
        if not self._check_dependencies():
            return False

        # 确认已安装目标工具链
        try:
            result = subprocess.run(
                ["rustup", "target", "list", "--installed"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            installed = result.stdout.split()
            if self.target not in installed:
                print(f"未检测到 Rust 目标 {self.target}，尝试安装...")
                try:
                    subprocess.run(["rustup", "target", "add", self.target], check=True)
                    print(f"Rust 目标 {self.target} 安装完成")
                except Exception as _:
                    print(f"自动安装 {self.target} 失败，请手动安装：rustup target add {self.target}")
        except FileNotFoundError:
            print("未找到 rustup，跳过目标检查。请确保已安装对应 target：aarch64-linux-android")
        except subprocess.CalledProcessError:
            print("查询 rustup 目标失败，继续尝试编译…")

        # 执行cargo fmt --check命令
        print("检查代码格式...")
        fmt_cmd = ["cargo", "fmt", "--check"]
        print(f"执行命令: {' '.join(fmt_cmd)}")

        try:
            result = subprocess.run(
                fmt_cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            print("代码格式检查通过！")
            if result.stdout:
                print(result.stdout)
        except FileNotFoundError:
            print("未找到 cargo-fmt，跳过格式检查。可通过 'rustup component add rustfmt' 安装。")
        except subprocess.CalledProcessError as e:
            print(f"代码格式检查失败：{e}")
            if hasattr(e, "stderr") and e.stderr:
                print(f"错误输出：{e.stderr}")

            # 自动修复代码格式
            print("正在自动修复代码格式...")
            fmt_fix_cmd = ["cargo", "fmt"]
            print(f"执行命令: {' '.join(fmt_fix_cmd)}")

            try:
                fix_result = subprocess.run(
                    fmt_fix_cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                print("代码格式修复成功！")
                if fix_result.stdout:
                    print(fix_result.stdout)

                # 再次检查格式是否正确
                print("再次检查代码格式...")
                recheck_result = subprocess.run(
                    fmt_cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                print("代码格式检查通过！")

            except FileNotFoundError:
                print("未找到 cargo-fmt，跳过自动修复。可通过 'rustup component add rustfmt' 安装。")
                # 继续流程
            except subprocess.CalledProcessError as fix_e:
                print(f"代码格式修复失败：{fix_e}")
                if hasattr(fix_e, "stderr") and fix_e.stderr:
                    print(f"错误输出：{fix_e.stderr}")
                print("请手动运行 'cargo fmt' 来格式化代码")
                return False

        # 执行cargo clippy代码检查
        print("执行代码质量检查...")
        clippy_cmd = ["cargo", "clippy", "--", "-D", "warnings"]
        print(f"执行命令: {' '.join(clippy_cmd)}")

        try:
            result = subprocess.run(
                clippy_cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            print("代码质量检查通过！")
            if result.stdout:
                print(result.stdout)
        except FileNotFoundError:
            print("未找到 cargo-clippy，跳过代码质量检查。可通过 'rustup component add clippy' 安装。")
        except subprocess.CalledProcessError as e:
            print(f"代码质量检查失败：{e}")
            if hasattr(e, "stderr") and e.stderr:
                print(f"错误输出：{e.stderr}")
            if hasattr(e, "stdout") and e.stdout:
                print(f"标准输出：{e.stdout}")
            print("请修复上述警告和错误后重新编译")
            return False

        # 执行cargo build命令
        cmd = ["cargo", "build", "--release"]
        # 若指定 target，与 cargo build 一起传入
        if self.target:
            cmd.extend(["--target", self.target])
        print(f"执行命令: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            print("编译成功！")
            if result.stdout:
                print(result.stdout)
            return True
        except subprocess.CalledProcessError as e:
            print(f"编译失败：{e}")
            if hasattr(e, "stderr") and e.stderr:
                print(f"错误输出：{e.stderr}")
            return False

    def copy_binary(self):
        """复制编译后的二进制文件到输出目录"""
        # 根据目标三元组决定后缀（Android/Linux 无后缀，Windows 为 .exe）
        exe_suffix = self._binary_suffix()
        source_path = f"target/{self.target}/release/{self.binary_name}{exe_suffix}"

        if not os.path.exists(source_path):
            print(f"错误：编译输出文件未找到：{source_path}")
            return False

        # 创建输出目录
        os.makedirs(self.output_dir, exist_ok=True)

        # 复制文件
        dest_path = f"{self.output_dir}/{self.binary_name}{exe_suffix}"
        shutil.copy2(source_path, dest_path)

        # 显示文件大小
        file_size = os.path.getsize(dest_path)
        print(f"二进制文件已复制到：{dest_path}")
        print(f"文件大小：{file_size:,} 字节")

        return True

    def clean(self):
        """清理编译输出"""
        print("清理编译输出...")

        # 清理cargo输出
        try:
            subprocess.run(
                ["cargo", "clean"], check=True, encoding="utf-8", errors="ignore"
            )
            print("Cargo清理完成")
        except subprocess.CalledProcessError as e:
            print(f"Cargo清理失败：{e}")

        # 清理输出目录
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)
            print(f"输出目录已清理：{self.output_dir}")

    def build_only_flow(self):
        """执行编译流程（默认行为）"""
        print("=" * 50)
        print("fpid 编译脚本")
        print("=" * 50)

        # 编译
        if not self.build():
            print("编译失败，停止执行")
            return False

        # 复制二进制文件
        if not self.copy_binary():
            print("复制二进制文件失败，停止执行")
            return False

        print("=" * 50)
        print("编译完成！")
        print("=" * 50)
        return True



def main():
    parser = argparse.ArgumentParser(description="fpid 编译脚本")
    parser.add_argument("--clean", action="store_true", help="清理编译输出")
    parser.add_argument(
        "--config", default="build_config.toml", help="配置文件路径"
    )

    args = parser.parse_args()

    builder = GPUGovernorBuilder(args.config)

    if args.clean:
        builder.clean()
        return

    # 默认：仅编译流程
    if not builder.build_only_flow():
        sys.exit(1)


if __name__ == "__main__":
    main()
