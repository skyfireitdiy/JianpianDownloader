#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
import os
import re
from m3u8 import M3U8
import time
import concurrent.futures
from urllib.parse import urljoin
import shutil
from threading import Lock
import signal
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, DownloadColumn
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
import threading
from rich import box

console = Console()

class Video:
    """视频对象"""
    def __init__(self, title, detail_url):
        self.title = title  # 视频标题
        self.detail_url = detail_url  # 详情页URL
        self.episodes = []  # 剧集列表
        self.current_episode = None  # 当前选中的剧集
        self.poster = None  # 海报URL
        
    def get_episodes(self, downloader):
        """获取剧集列表"""
        self.episodes = downloader.get_play_urls(self.detail_url)
        return len(self.episodes) > 0
        
    def select_episode(self, index):
        """选择剧集"""
        if 0 <= index < len(self.episodes):
            self.current_episode = self.episodes[index]
            return True
        return False
        
    def get_episode_path(self, save_dir, episode_index):
        """获取剧集的保存路径"""
        if 0 <= episode_index < len(self.episodes):
            # 如果没有指定保存路径，使用默认路径
            if save_dir is None:
                save_dir = "downloads"
                
            # 创建保存路径（如果不存在）
            save_dir = os.path.expanduser(save_dir)  # 展开用户路径（如果有~）
            save_dir = os.path.abspath(save_dir)     # 转换为绝对路径
                
            # 构建完整的保存路径
            video_dir = os.path.join(save_dir, re.sub(r'[<>:"/\\|?*]', '', self.title))
            return os.path.join(video_dir, f"{self.episodes[episode_index]['title']}.mp4")
        return None
        
    def download(self, downloader, save_dir=None):
        """下载当前选中的剧集"""
        if not self.current_episode:
            print("请先选择要下载的剧集")
            return False
            
        # 如果没有指定保存路径，使用默认路径
        if save_dir is None:
            save_dir = "downloads"
            
        # 创建保存路径（如果不存在）
        save_dir = os.path.expanduser(save_dir)  # 展开用户路径（如果有~）
        save_dir = os.path.abspath(save_dir)     # 转换为绝对路径
            
        # 构建完整的保存路径
        video_dir = os.path.join(save_dir, re.sub(r'[<>:"/\\|?*]', '', self.title))
        save_path = os.path.join(video_dir, f"{self.current_episode['title']}.mp4")
            
        return downloader.download_movie(self.current_episode['url'], save_path)

class SpeedMonitor:
    def __init__(self):
        self.downloaded_bytes = 0
        self.start_time = time.time()
        self.lock = Lock()
        self.last_bytes = 0
        self.last_time = time.time()
        self.current_speed = 0
        
    def add_bytes(self, bytes_count):
        with self.lock:
            self.downloaded_bytes += bytes_count
            current_time = time.time()
            time_diff = current_time - self.last_time
            
            # 每0.5秒更新一次速度
            if time_diff >= 0.5:
                bytes_diff = self.downloaded_bytes - self.last_bytes
                self.current_speed = bytes_diff / time_diff
                self.last_bytes = self.downloaded_bytes
                self.last_time = current_time
            
    def get_speed(self):
        with self.lock:
            return self.current_speed
            
    def get_total_bytes(self):
        with self.lock:
            return self.downloaded_bytes
            
    def format_speed(self):
        speed = self.get_speed()
        if speed > 1024 * 1024:
            return f"{speed / (1024 * 1024):.2f} MB/s"
        elif speed > 1024:
            return f"{speed / 1024:.2f} KB/s"
        else:
            return f"{speed:.2f} B/s"

class MovieDownloader:
    def __init__(self, max_workers=48):
        self.base_url = "https://vodjp.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        self.max_workers = max_workers  # 最大并行下载数
        self.stop_flag = False  # 停止标志
        self.console = Console()
        self.output_lock = threading.Lock()  # 输出锁
        self.download_manager = None  # 下载管理器引用
        
    def set_download_manager(self, manager):
        """设置下载管理器引用"""
        self.download_manager = manager
        
    def print_progress(self, success_count, total_count, speed):
        """打印下载进度"""
        if self.download_manager and self.download_manager.status_display:
            return  # 如果正在显示状态，跳过进度输出
            
        with self.output_lock:
            print(f"\r下载进度: {success_count/total_count*100:5.1f}% "
                  f"({success_count:4d}/{total_count}) "
                  f"- {speed:>10}        ", 
                  end='', flush=True)
        
    def stop_download(self, signum=None, frame=None):
        """停止下载"""
        self.console.print("\n[yellow]接收到停止信号，正在停止下载...[/yellow]")
        self.stop_flag = True
        # 强制退出所有线程
        os._exit(0)
        
    def search_video(self, keyword):
        """搜索视频,返回Video对象列表"""
        videos = []
        page = 1
        
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=self.console
        ) as progress:
            search_task = progress.add_task(f"搜索: {keyword}", total=None)
            
            while True:
                search_url = f"{self.base_url}/jpsearch/{keyword}----------{page}---.html"
                try:
                    progress.update(search_task, description=f"搜索第{page}页: {keyword}")
                    response = requests.get(search_url, headers=self.headers, timeout=10)
                    
                    if response.status_code != 200:
                        self.console.print(f"[red]搜索失败: HTTP {response.status_code}[/red]")
                        break
                        
                    soup = BeautifulSoup(response.text, 'html.parser')
                    results = soup.find_all('li', class_='stui-vodlist__item')
                    
                    if not results:
                        break
                        
                    for item in results:
                        try:
                            link_elem = item.find('a', class_='stui-vodlist__thumb')
                            title = link_elem.get('title', '').strip()
                            link = link_elem.get('href', '')
                            poster = link_elem.get('data-original', '')  # 获取海报图片URL
                            
                            if link:
                                link = self.base_url + link
                                video = Video(title, link)
                                video.poster = poster  # 保存海报URL
                                videos.append(video)
                        except Exception as e:
                            self.console.print(f"[yellow]解析视频信息失败: {e}[/yellow]")
                            continue
                    
                    page += 1
                    
                except Exception as e:
                    self.console.print(f"[red]搜索失败: {e}[/red]")
                    break
                    
        if videos:
            self.console.print("\n[bold green]搜索结果:[/bold green]")
            
            # 创建表格
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("序号", style="cyan", width=6)
            table.add_column("片名", style="white")
            table.add_column("海报", style="blue")
            
            for i, video in enumerate(videos, 1):
                poster_info = "[blue]📷[/blue] " + (video.poster if video.poster else "无海报")
                table.add_row(
                    str(i),
                    video.title,
                    poster_info
                )
            
            self.console.print(table)
            
        return videos
        
    def get_play_urls(self, movie_url):
        """获取电影播放链接"""
        try:
            print(f"正在获取播放地址: {movie_url}")
            response = requests.get(movie_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 直接获取播放列表
            play_list = soup.find('div', id='playlist1')
            if not play_list:
                print("未找到播放列表")
                return []
            
            episodes = []
            links = play_list.find_all('a')
            for link in links:
                title = link.text.strip()
                url = link.get('href', '')
                if url:
                    url = self.base_url + url
                episodes.append({
                    'title': title,
                    'url': url
                })
            
            return episodes
            
        except Exception as e:
            print(f"获取播放地址失败: {str(e)}")
            return []
    
    def download_movie(self, play_url, save_path):
        """下载视频"""
        temp_dir = None
        try:
            # 解析视频地址
            self.console.print("[yellow]正在解析视频地址...[/yellow]")
            response = requests.get(play_url, headers=self.headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            video_url = self._extract_video_url(soup)
            
            if not video_url:
                self.console.print("[red]未找到视频地址[/red]")
                return False
            
            self.console.print("[yellow]获取m3u8内容...[/yellow]")
            
            # 下载主m3u8文件
            m3u8_response = requests.get(video_url, headers=self.headers)
            m3u8_response.raise_for_status()
            
            # 解析主m3u8文件
            m3u8_obj = M3U8(m3u8_response.text)
            
            # 获取子m3u8地址
            if m3u8_obj.is_endlist:
                segments = m3u8_obj.segments
            else:
                if not m3u8_obj.playlists:
                    self.console.print("[red]未找到播放列表[/red]")
                    return False
                    
                sub_m3u8_uri = m3u8_obj.playlists[0].uri
                sub_m3u8_url = urljoin(video_url, sub_m3u8_uri)
                self.console.print("[yellow]获取子m3u8内容...[/yellow]")
                
                sub_m3u8_response = requests.get(sub_m3u8_url, headers=self.headers)
                sub_m3u8_response.raise_for_status()
                
                sub_m3u8_obj = M3U8(sub_m3u8_response.text)
                segments = sub_m3u8_obj.segments
            
            if not segments:
                self.console.print("[red]未找到视频段[/red]")
                return False
            
            self.console.print(f"[green]找到 {len(segments)} 个视频片段[/green]")
            
            # 创建临时目录和进度文件
            temp_dir = f"{save_path}.downloading"
            os.makedirs(temp_dir, exist_ok=True)
            progress_file = os.path.join(temp_dir, "progress.txt")
            downloaded_segments = set()
            if os.path.exists(progress_file):
                with open(progress_file, 'r') as f:
                    downloaded_segments = set(int(x.strip()) for x in f.readlines())
                self.console.print(f"[blue]找到已下载的片段: {len(downloaded_segments)}/{len(segments)}[/blue]")
            
            # 获取未下载的片段
            remaining_segments = [(i, seg) for i, seg in enumerate(segments) if i not in downloaded_segments]
            
            if not remaining_segments:
                self.console.print("[green]所有片段已下载完成[/green]")
            else:
                self.console.print(f"[yellow]开始下载剩余 {len(remaining_segments)} 个片段...[/yellow]")
                self.console.print("[cyan]提示: 按 Ctrl+C 可以暂停下载，下次继续时会从断点续传[/cyan]")
                
                success_count = len(downloaded_segments)
                speed_monitor = SpeedMonitor()
                
                def download_segment(args):
                    if self.stop_flag:
                        return None, False
                    
                    index, segment = args
                    try:
                        ts_url = urljoin(video_url, segment.uri)
                        ts_path = os.path.join(temp_dir, f"{index:05d}.ts")
                        
                        if index in downloaded_segments and os.path.exists(ts_path):
                            return index, True
                        
                        ts_response = requests.get(ts_url, headers=self.headers, stream=True)
                        ts_response.raise_for_status()
                        
                        downloaded_size = 0
                        with open(ts_path, 'wb') as f:
                            for chunk in ts_response.iter_content(chunk_size=8192):
                                if self.stop_flag:
                                    return None, False
                                if chunk:
                                    f.write(chunk)
                                    downloaded_size += len(chunk)
                                    speed_monitor.add_bytes(len(chunk))
                        
                        if downloaded_size > 0:
                            with open(progress_file, 'a') as f:
                                f.write(f"{index}\n")
                            return index, True
                        else:
                            if os.path.exists(ts_path):
                                os.remove(ts_path)
                            return index, False
                            
                    except Exception as e:
                        if not self.stop_flag:
                            self.console.print(f"[red]片段 {index+1} 下载出错: {e}[/red]")
                        if os.path.exists(ts_path):
                            os.remove(ts_path)
                        return None, False
                
                try:
                    # 并行下载
                    with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        futures = [executor.submit(download_segment, args) for args in remaining_segments]
                        for future in concurrent.futures.as_completed(futures):
                            if self.stop_flag:
                                for f in futures:
                                    f.cancel()
                                executor._threads.clear()
                                concurrent.futures.thread._threads_queues.clear()
                                raise KeyboardInterrupt()
                            
                            result = future.result()
                            if result:
                                index, success = result
                                if success:
                                    success_count += 1
                                    self.print_progress(success_count, len(segments), speed_monitor.format_speed())
                
                    with self.output_lock:
                        print()  # 打印换行
                    
                    if success_count == len(segments):
                        # 暂停进度输出
                        if self.download_manager:
                            self.download_manager.status_display = True
                        
                        try:
                            # 清除当前行
                            print("\r" + " " * 100 + "\r", end="", flush=True)
                            
                            # 合并文件
                            self.console.print("\n[yellow]正在合并视频片段...[/yellow]")
                            os.makedirs(os.path.dirname(save_path), exist_ok=True)
                            with open(save_path, 'wb') as outfile:
                                for i in range(len(segments)):
                                    ts_path = os.path.join(temp_dir, f"{i:05d}.ts")
                                    if os.path.exists(ts_path):
                                        with open(ts_path, 'rb') as infile:
                                            outfile.write(infile.read())
                            
                            # 删除临时文件
                            self.console.print("[yellow]清理临时文件...[/yellow]")
                            shutil.rmtree(temp_dir)
                            temp_dir = None
                            
                            # 检查文件大小
                            file_size = os.path.getsize(save_path)
                            if file_size == 0:
                                self.console.print("[red]下载失败: 文件大小为0[/red]")
                                os.remove(save_path)
                                return False
                            
                            self.console.print(Panel(f"[green]下载完成: {save_path}\n文件大小: {file_size / (1024*1024):.2f} MB[/green]"))
                            return True
                        finally:
                            # 恢复进度输出
                            if self.download_manager:
                                self.download_manager.status_display = False
                    else:
                        self.console.print("\n[yellow]下��未完成，下次运行时将继续下载[/yellow]")
                        return False
                        
                except KeyboardInterrupt:
                    with self.output_lock:
                        self.console.print("\n[yellow]下载已暂停，下次运行时将继续下载[/yellow]")
                    return False
                    
        except Exception as e:
            if not self.stop_flag:
                with self.output_lock:
                    self.console.print(f"[red]下载失败: {str(e)}[/red]")
            if os.path.exists(save_path):
                os.remove(save_path)
            return False
        finally:
            self.stop_flag = False
    
    def _extract_video_url(self, soup):
        """从播放页面提取视频地址"""
        try:
            # 查找包含播放器配置的script标签
            scripts = soup.find_all('script')
            for script in scripts:
                script_text = script.string
                if script_text and 'player_aaaa' in script_text:
                    # 使用正则表达式提取m3u8地址
                    import re
                    match = re.search(r'"url":"([^"]+)"', script_text)
                    if match:
                        m3u8_url = match.group(1)
                        # ��理转义的url
                        m3u8_url = m3u8_url.replace('\\/', '/')
                        print(f"找到m3u8地址: {m3u8_url}")
                        return m3u8_url
                    
            print("未找到播放器配置信息")
            
        except Exception as e:
            print(f"提取视频地址失败: {str(e)}")
        return ''
    
    def get_movie_info(self, movie_url):
        """获取影片详细信息"""
        try:
            response = requests.get(movie_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 提取基本信息
            info = {}
            
            # 标题和评分
            title_elem = soup.find('h3', class_='title')
            if title_elem:
                info['title'] = title_elem.text.split('span')[0].strip()
                score_elem = title_elem.find('span', class_='score')
                if score_elem:
                    info['score'] = score_elem.text.strip()
            
            # 提取类型、地区、年份等信息
            data_elems = soup.find_all('p', class_='data')
            for elem in data_elems:
                text = elem.get_text(strip=True)
                if '类型：' in text:
                    info['type'] = text.split('类型：')[1].split('地区：')[0].strip()
                if '地区：' in text:
                    info['area'] = text.split('地区：')[1].split('年份：')[0].strip()
                if '年份：' in text:
                    info['year'] = text.split('年份：')[1].strip()
                if '主演：' in text:
                    info['actors'] = text.split('主演：')[1].strip()
                if '导演：' in text:
                    info['director'] = text.split('导演：')[1].strip()
            
            # 提取简介
            desc_elem = soup.find('div', class_='stui-content__desc')
            if desc_elem:
                info['description'] = desc_elem.text.strip()
            
            # 打印影片信息
            self.console.print("\n[bold yellow]影片信息:[/bold yellow]")
            if 'title' in info:
                self.console.print(f"[bold]片名:[/bold] {info['title']}")
            if 'score' in info:
                self.console.print(f"[bold]评分:[/bold] {info['score']}")
            if 'type' in info:
                self.console.print(f"[bold]类型:[/bold] {info['type']}")
            if 'area' in info:
                self.console.print(f"[bold]地区:[/bold] {info['area']}")
            if 'year' in info:
                self.console.print(f"[bold]年份:[/bold] {info['year']}")
            if 'director' in info:
                self.console.print(f"[bold]导演:[/bold] {info['director']}")
            if 'actors' in info:
                self.console.print(f"[bold]主演:[/bold] {info['actors']}")
            if 'description' in info:
                self.console.print(f"\n[bold]剧情简介:[/bold]\n{info['description']}")
            
            return info
            
        except Exception as e:
            self.console.print(f"[red]获取影片信息失败: {e}[/red]")
            return None

class DownloadManager:
    """下载管理器"""
    def __init__(self):
        self.downloads = {}  # 保存所有下载任务
        self.lock = threading.Lock()
        self.output_lock = threading.Lock()  # 输出锁
        self.status_display = False  # 状态显示标志
        
    def add_download(self, video, episode_index, save_dir, downloader):
        """添加下载任务"""
        with self.lock:
            task_id = f"{video.title}_{episode_index}"
            if task_id in self.downloads:
                return False
                
            # 检查文件是否已经存在
            save_path = video.get_episode_path(save_dir, episode_index)
            if save_path and os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                # 文件已存在且大小大于0，直接标记为已完成
                self.downloads[task_id] = {
                    'thread': None,
                    'status': 'completed',
                    'progress': 100,
                    'video': video,
                    'episode': video.episodes[episode_index],
                    'save_dir': save_dir
                }
                return True
                
            # 创建新的线程来处理下载
            thread = threading.Thread(
                target=self._download_task,
                args=(task_id, video, episode_index, save_dir, downloader),
                daemon=True
            )
            
            self.downloads[task_id] = {
                'thread': thread,
                'status': 'pending',
                'progress': 0,
                'video': video,
                'episode': video.episodes[episode_index],
                'save_dir': save_dir
            }
            
            thread.start()
            return True
            
    def _download_task(self, task_id, video, episode_index, save_dir, downloader):
        """下载任务处理函数"""
        try:
            with self.lock:
                self.downloads[task_id]['status'] = 'downloading'
                
            if video.select_episode(episode_index):
                success = video.download(downloader, save_dir)
                
                with self.lock:
                    self.downloads[task_id]['status'] = 'completed' if success else 'failed'
            else:
                with self.lock:
                    self.downloads[task_id]['status'] = 'failed'
                    
        except Exception as e:
            with self.lock:
                self.downloads[task_id]['status'] = 'failed'
                self.downloads[task_id]['error'] = str(e)
                
    def get_status(self):
        """获取所有下载任务的状态"""
        with self.lock:
            return {
                task_id: {
                    'status': info['status'],
                    'progress': info['progress'],
                    'video': info['video'].title,
                    'episode': info['episode']['title'],
                    'save_dir': info['save_dir']
                }
                for task_id, info in self.downloads.items()
            }
            
    def is_all_completed(self):
        """检查是否所有任务都已完成"""
        with self.lock:
            return all(info['status'] in ['completed', 'failed'] 
                      for info in self.downloads.values())
                      
    def get_active_count(self):
        """获取正在下载的任务数"""
        with self.lock:
            return sum(1 for info in self.downloads.values() 
                      if info['status'] == 'downloading')
                      
    def print_status(self):
        """打印下载状态"""
        statuses = self.get_status()
        if not statuses:
            return
            
        with self.output_lock:
            self.status_display = True  # 设置状态显示标志
            # 清除当前行
            print("\r" + " " * 100 + "\r", end="", flush=True)
            console.print("\n[bold green]下载任务状态[/bold green]")
            
            # 创建状态表格
            table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("序号", style="cyan", width=6)
            table.add_column("视频", style="white")
            table.add_column("剧集", style="white")
            table.add_column("状态", style="white")
            table.add_column("保存位置", style="white")
            
            for i, (task_id, info) in enumerate(statuses.items(), 1):
                status_style = {
                    'pending': '[yellow]等待中[/yellow]',
                    'downloading': '[blue]下载中[/blue]',
                    'completed': '[green]已完成[/green]',
                    'failed': '[red]失败[/red]'
                }.get(info['status'], info['status'])
                
                table.add_row(
                    str(i),
                    info['video'],
                    info['episode'],
                    status_style,
                    info['save_dir']
                )
            
            console.print(table)
            console.print()  # 添加一个空行
            self.status_display = False  # 清除状态显示标志

def parse_episode_ranges(input_str, max_episodes):
    """解析剧集范围
    支持的格式:
    - 单个数字: "1"
    - 逗号分隔: "1,2,3"
    - 范围: "1-3"
    - 混合: "1-3,5,7-9"
    """
    result = set()
    try:
        # 按逗号分割
        parts = input_str.replace('，', ',').split(',')
        for part in parts:
            part = part.strip()
            if '-' in part:
                # 处理范围
                start, end = map(int, part.split('-'))
                if start > end:
                    start, end = end, start
                if start < 1 or end > max_episodes:
                    raise ValueError(f"剧集范围 {start}-{end} 超出有效范围 1-{max_episodes}")
                result.update(range(start-1, end))
            else:
                # 处理单个数字
                num = int(part)
                if num < 1 or num > max_episodes:
                    raise ValueError(f"剧集 {num} 超出有效范围 1-{max_episodes}")
                result.add(num-1)
        return sorted(list(result))
    except ValueError as e:
        if str(e).startswith("剧集"):
            raise
        raise ValueError("输入格式无效，请使用数字、逗号和字符，例如: 1-3,5,7-9")

def main():
    # 设置信号处理
    def signal_handler(signum, frame):
        console.print("\n[yellow]正在退出程序...[/yellow]")
        os._exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 创建下载管理器
        download_manager = DownloadManager()
        
        # 获取并行下载数
        try:
            max_workers = int(input("请输入并行下载数(默认48): ") or "48")
            if max_workers < 1:
                print("并行数量必须大于0使用默认值48")
                max_workers = 48
        except ValueError:
            print("输入无效，使用默认值48")
            max_workers = 48
            
        downloader = MovieDownloader(max_workers=max_workers)
        downloader.set_download_manager(download_manager)  # 设置下载管理器引用
        
        while True:
            # 搜索视频
            keyword = input("\n请输入要搜索的视频名称（直接回车查看下载状态，输入 q 退出）: ")
            if not keyword:
                download_manager.print_status()
                continue
            if keyword.lower() == 'q':
                break
                
            videos = downloader.search_video(keyword)
            
            if not videos:
                print("未找到相关视频")
                continue
                
            # 选择视频
            choice = int(input("\n请输入要下载的视频编号: ")) - 1
            if 0 <= choice < len(videos):
                video = videos[choice]
                
                # 获取并显示影片信息
                downloader.get_movie_info(video.detail_url)
                
                # 获取剧集列表
                if not video.get_episodes(downloader):
                    print("获取剧集列表失败")
                    continue
                    
                # 显示剧集列表
                console.print(f"\n[bold green]剧集列表[/bold green] [blue](共{len(video.episodes)}集)[/blue]")
                
                # 创建表格
                table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
                table.add_column("序号", style="cyan", width=6, justify="center")
                table.add_column("剧集", style="white")
                
                # 计算每行显示的列数
                COLUMNS = 4
                rows = []
                current_row = []
                
                for i, ep in enumerate(video.episodes, 1):
                    current_row.extend([str(i), ep['title']])
                    if len(current_row) == COLUMNS * 2:
                        rows.append(current_row)
                        current_row = []
                
                if current_row:
                    while len(current_row) < COLUMNS * 2:
                        current_row.extend(['', ''])
                    rows.append(current_row)
                
                # 添加数据到表格
                for row in rows:
                    table.add_row(*row)
                
                console.print(table)
                console.print("\n[cyan]提示: 支持范围选择，例如: 1-3,5,7-9[/cyan]")
                    
                # 选择要下载的剧集
                while True:
                    try:
                        ep_choice = input("\n请输入要下载的剧集编号: ")
                        ep_choices = parse_episode_ranges(ep_choice, len(video.episodes))
                        break
                    except ValueError as e:
                        print(f"错误: {str(e)}")
                        continue
                
                # 设置下载路径
                default_path = "downloads"
                console.print(f"\n[cyan]默认下载路径: {os.path.abspath(default_path)}[/cyan]")
                save_dir = input("请输入保存路径（直接回车使用默认路径）: ").strip() or default_path
                
                # 显示实际下载路径
                save_dir = os.path.expanduser(save_dir)
                save_dir = os.path.abspath(save_dir)
                console.print(f"[green]文件将保存到: {save_dir}[/green]")
                
                # 确保目录存在
                try:
                    os.makedirs(save_dir, exist_ok=True)
                    if not os.access(save_dir, os.W_OK):
                        raise PermissionError("没有写入权限")
                except Exception as e:
                    console.print(f"[red]创建目录失败: {str(e)}[/red]")
                    continue
                
                # 添加下载任务
                for ep_idx in ep_choices:
                    if download_manager.add_download(video, ep_idx, save_dir, downloader):
                        console.print(f"[green]已添加下载任务: {video.episodes[ep_idx]['title']}[/green]")
                    else:
                        console.print(f"[yellow]任务已存在: {video.episodes[ep_idx]['title']}[/yellow]")
                
                # ���示当前下载状态
                download_manager.print_status()
            else:
                print("无效的选择")
                
    except KeyboardInterrupt:
        console.print("\n[yellow]用户取消操作，正在退出...[/yellow]")
    except Exception as e:
        console.print(f"\n[red]发生错误: {str(e)}[/red]")
    finally:
        console.print("[yellow]程序已退出[/yellow]")

if __name__ == "__main__":
    main() 