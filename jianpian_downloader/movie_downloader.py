#!/usr/bin/env python3
import os
import sys
import re
import time
import signal
import threading
import concurrent.futures
import shutil
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from m3u8 import M3U8
from urllib.parse import urljoin
from threading import Lock
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, DownloadColumn
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box
import json
from datetime import datetime
import resource

# 设置文件描述符软限制和硬限制
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))

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
        try:
            if not self.detail_url:
                return False
            
            response = requests.get(self.detail_url, headers=downloader.headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            episode_list = soup.find('ul', class_='stui-content__playlist')
            if not episode_list:
                return False
            
            episodes = []
            for item in episode_list.find_all('li'):
                link = item.find('a')
                if link:
                    episodes.append({
                        'title': link.text.strip(),
                        'url': urljoin(downloader.base_url, link['href'])
                    })
            
            if episodes:
                self.episodes = episodes
                return True
            
            return False
            
        except Exception as e:
            return False
        
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
        self.last_update = time.time()
        
    def add_bytes(self, bytes_count):
        with self.lock:
            current_time = time.time()
            self.downloaded_bytes += bytes_count
            
            # 每0.5秒更新一次速度
            if current_time - self.last_update >= 0.5:
                time_diff = current_time - self.last_time
                if time_diff > 0:
                    bytes_diff = self.downloaded_bytes - self.last_bytes
                    self.current_speed = bytes_diff / time_diff
                    self.last_bytes = self.downloaded_bytes
                    self.last_time = current_time
                self.last_update = current_time
            
    def format_speed(self):
        with self.lock:
            if self.current_speed == 0:
                return "-"
            elif self.current_speed > 1024 * 1024:
                return f"{self.current_speed / (1024 * 1024):.2f} MB/s"
            elif self.current_speed > 1024:
                return f"{self.current_speed / 1024:.2f} KB/s"
            else:
                return f"{self.current_speed:.2f} B/s"

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
        self.executor = None  # 线程池引用
        
    def set_download_manager(self, manager):
        """设置下载管理器引用"""
        self.download_manager = manager
        
    def print_progress(self, success_count, total_count, speed):
        """打印下载进度"""
        return
        
    def stop_download(self, signum=None, frame=None):
        """停止下载"""
        self.console.print("\n[yellow]正在停止所有下载...[/yellow]")
        self.stop_flag = True
        # 强制退出
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
            # 检查是否存在未完成的下载
            temp_dir = f"{save_path}.downloading"
            progress_file = os.path.join(temp_dir, "progress.txt")
            downloaded_segments = set()
            
            # 如果存在临时目录，说明是断点续传
            if os.path.exists(temp_dir):
                if os.path.exists(progress_file):
                    with open(progress_file, 'r') as f:
                        downloaded_segments = set(int(x.strip()) for x in f.readlines())
                    console.print(f"[green]发现未完成的下载，已下载 {len(downloaded_segments)} 个分片[/green]")
            else:
                os.makedirs(temp_dir, exist_ok=True)

            # 解析视频地址
            response = requests.get(play_url, headers=self.headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            video_url = self._extract_video_url(soup)
            
            if not video_url:
                return False
            
            # 下载主m3u8文件
            m3u8_response = requests.get(video_url, headers=self.headers)
            m3u8_response.raise_for_status()
            
            # 解m3u8文件
            m3u8_obj = M3U8(m3u8_response.text)
            
            # 获取子m3u8地址
            if m3u8_obj.is_endlist:
                segments = m3u8_obj.segments
            else:
                if not m3u8_obj.playlists:
                    return False
                    
                sub_m3u8_uri = m3u8_obj.playlists[0].uri
                sub_m3u8_url = urljoin(video_url, sub_m3u8_uri)
                
                sub_m3u8_response = requests.get(sub_m3u8_url, headers=self.headers)
                sub_m3u8_response.raise_for_status()
                
                sub_m3u8_obj = M3U8(sub_m3u8_response.text)
                segments = sub_m3u8_obj.segments
            
            if not segments:
                return False

            # 获取未下载的片段
            remaining_segments = [(i, seg) for i, seg in enumerate(segments) if i not in downloaded_segments]
            
            if not remaining_segments:
                # 如果所有分片都已下载，直接进行合并
                success_count = len(segments)
            else:
                success_count = len(downloaded_segments)
                speed_monitor = SpeedMonitor()
                total_segments = len(segments)
                
                # 获取任务ID以更新状态
                task_id = None
                if self.download_manager:
                    for tid, info in self.download_manager.downloads.items():
                        if info.get('save_path') == save_path:
                            task_id = tid
                            break

                def update_progress():
                    if task_id and self.download_manager:
                        with self.download_manager.lock:
                            task = self.download_manager.downloads.get(task_id)
                            if task and task['status'] == 'downloading':
                                task['progress'] = (success_count / total_segments) * 100
                                task['speed'] = speed_monitor.format_speed()

                def download_segment(args):
                    if self.stop_flag:
                        return None, False
                    
                    index, segment = args
                    ts_path = os.path.join(temp_dir, f"{index:05d}.ts")
                    
                    try:
                        if index in downloaded_segments and os.path.exists(ts_path):
                            return index, True
                        
                        ts_url = urljoin(video_url, segment.uri)
                        ts_response = requests.get(ts_url, headers=self.headers, stream=True)
                        ts_response.raise_for_status()
                        
                        downloaded_size = 0
                        # 使用 with 语句确保文件正确关闭
                        with open(ts_path, 'wb') as f:
                            for chunk in ts_response.iter_content(chunk_size=8192):
                                if self.stop_flag:
                                    return None, False
                                if chunk:
                                    f.write(chunk)
                                    downloaded_size += len(chunk)
                                    speed_monitor.add_bytes(len(chunk))
                                    update_progress()
                        
                        if downloaded_size > 0:
                            # 使用 with 语句确保文件正确关闭
                            with open(progress_file, 'a') as f:
                                f.write(f"{index}\n")
                            return index, True
                        else:
                            if os.path.exists(ts_path):
                                os.remove(ts_path)
                            return index, False
                            
                    except Exception as e:
                        if os.path.exists(ts_path):
                            os.remove(ts_path)
                        return None, False

                try:
                    # 限制并发数，避免打开太多文件
                    max_concurrent = min(self.max_workers, 32)  # 限制最大并发数为32
                    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
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
                                    update_progress()

                    # 合并文件
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    with open(save_path, 'wb') as outfile:
                        for i in range(len(segments)):
                            ts_path = os.path.join(temp_dir, f"{i:05d}.ts")
                            if os.path.exists(ts_path):
                                # 使用 with 语句确保文件正确关闭
                                with open(ts_path, 'rb') as infile:
                                    outfile.write(infile.read())
                    
                    # 检查文件大小
                    file_size = os.path.getsize(save_path)
                    if file_size == 0:
                        os.remove(save_path)
                        if os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                        return False
                    
                    # 下载成功后删除临时目录
                    if os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                    return True
                        
                except KeyboardInterrupt:
                    return False
                    
        except Exception as e:
            console.print(f"[red]下载失败: {str(e)}[/red]")
            return False
        finally:
            self.stop_flag = False
    
    def _extract_video_url(self, soup):
        """从播放页面取视频地址"""
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
                        m3u8_url = m3u8_url.replace('\\/', '/')
                        return m3u8_url
                    
        except Exception as e:
            pass
        return ''
    
    def get_movie_info(self, movie_url):
        """获取影片详细信息"""
        try:
            response = requests.get(movie_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            info = {}
            
            # 提取基本信息
            title_elem = soup.find('h3', class_='title')
            if title_elem:
                try:
                    title_parts = title_elem.text.split('span')
                    if title_parts:
                        info['title'] = title_parts[0].strip()
                    score_elem = title_elem.find('span', class_='score')
                    if score_elem:
                        info['score'] = score_elem.text.strip()
                except Exception:
                    pass
            
            # 提取其他信息
            data_elems = soup.find_all('p', class_='data')
            for elem in data_elems:
                try:
                    text = elem.get_text(strip=True)
                    if '类型：' in text and '地区：' in text:
                        info['type'] = text.split('类型：')[1].split('地区：')[0].strip()
                    if '地区：' in text and '年份：' in text:
                        info['area'] = text.split('地区：')[1].split('年份：')[0].strip()
                    if '年份：' in text:
                        info['year'] = text.split('年份：')[1].strip()
                    if '主演：' in text:
                        info['actors'] = text.split('主演：')[1].strip()
                    if '导演：' in text:
                        info['director'] = text.split('导演：')[1].strip()
                except Exception:
                    continue
            
            # 提取简介
            desc_elem = soup.find('div', class_='stui-content__desc')
            if desc_elem:
                info['description'] = desc_elem.text.strip()
            
            return info
            
        except Exception as e:
            return {}

class DownloadManager:
    """下载管理器"""
    def __init__(self):
        self.downloads = {}  # 保存所有下载任务
        self.lock = threading.Lock()
        self.output_lock = threading.Lock()  # 输出锁
        self.status_display = False  # 状态显示标志
        self.task_store = TaskStore()  # 任务存储器
        self.stop_flag = False  # 停止标志
        self.auto_save_thread = None  # 自动保存线程
        
    def start_auto_save(self):
        """启动自动保存线程"""
        if self.auto_save_thread is None:
            self.auto_save_thread = threading.Thread(target=self._auto_save_tasks, daemon=True)
            self.auto_save_thread.start()
        
    def _auto_save_tasks(self):
        """定期自动保存任务状态"""
        while not self.stop_flag:
            try:
                with self.lock:
                    self.task_store.save_tasks(self.downloads)
            except Exception as e:
                console.print(f"[yellow]自动保存任务状态失败: {str(e)}[/yellow]")
            time.sleep(5) 
            
    def stop(self):
        """停止下载管理器"""
        self.stop_flag = True
        # 确保最后一次保存
        with self.lock:
            self.task_store.save_tasks(self.downloads)
        
    def restore_tasks(self, downloader):
        """恢复未完成的下载任务"""
        stored_tasks = self.task_store.load_tasks()
        restored_count = 0
        
        for task_id, task_info in stored_tasks.items():
            try:
                # 检查临时目录
                temp_dir = f"{task_info['save_path']}.downloading"
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir, exist_ok=True)
                
                # 创建Video对象
                video = Video(task_info['video_title'], task_info['video_url'])
                
                # 获取剧集列表
                if not video.get_episodes(downloader):
                    console.print(f"[yellow]恢复任务失败 {task_id}: 无法获取剧集列表[/yellow]")
                    continue
                
                # 查找对应的剧集
                episode_index = None
                for i, ep in enumerate(video.episodes):
                    if ep['url'] == task_info['episode_url']:
                        episode_index = i
                        break
                
                if episode_index is None:
                    console.print(f"[yellow]恢复任务失败 {task_id}: 找不到对应剧集[/yellow]")
                    continue
                
                # 选择剧集
                if not video.select_episode(episode_index):
                    console.print(f"[yellow]恢复任务失败 {task_id}: 选择剧集失败[/yellow]")
                    continue
                
                # 添加到下载队列，保持原始状态和进度
                thread = threading.Thread(
                    target=self._download_task,
                    args=(task_id, video, episode_index, task_info['save_dir'], downloader),
                    daemon=True
                )
                
                with self.lock:
                    self.downloads[task_id] = {
                        'thread': thread,
                        'status': task_info['status'],  # 保持原始状态
                        'progress': task_info['progress'],  # 保持原始进度
                        'speed': '0 B/s',
                        'video': video,
                        'episode': video.episodes[episode_index],
                        'save_dir': task_info['save_dir'],
                        'save_path': task_info['save_path'],
                        'created_at': task_info['created_at']
                    }
                    
                thread.start()
                restored_count += 1
                console.print(f"[green]已恢复任务: {video.title} - {video.episodes[episode_index]['title']} (进度: {task_info['progress']:.1f}%)[/green]")
                
            except Exception as e:
                console.print(f"[yellow]恢复任务 {task_id} 失败: {str(e)}[/yellow]")
                continue
        
        return restored_count
        
    def add_download(self, video, episode_index, save_dir, downloader):
        """添加下载任务"""
        with self.lock:
            task_id = f"{video.title}_{episode_index}"
            if task_id in self.downloads:
                return False
                
            # 检查文件是否已经存在
            save_path = video.get_episode_path(save_dir, episode_index)
            if save_path and os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                self.downloads[task_id] = {
                    'thread': None,
                    'status': 'completed',
                    'progress': 100,
                    'speed': '-',
                    'video': video,
                    'episode': video.episodes[episode_index],
                    'save_dir': save_dir,
                    'save_path': save_path,
                    'created_at': datetime.now().isoformat()
                }
                self.task_store.save_tasks(self.downloads)
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
                'speed': '0 B/s',
                'video': video,
                'episode': video.episodes[episode_index],
                'save_dir': save_dir,
                'save_path': save_path,
                'created_at': datetime.now().isoformat()
            }
            
            thread.start()
            self.task_store.save_tasks(self.downloads)
            return True
            
    def _download_task(self, task_id, video, episode_index, save_dir, downloader):
        """下载任务处理函数"""
        max_retries = 3  # 最大重试次数
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                with self.lock:
                    if task_id not in self.downloads:
                        return
                        
                    if retry_count > 0:
                        self.downloads[task_id]['status'] = 'retrying'
                    else:
                        self.downloads[task_id]['status'] = 'downloading'
                    self.task_store.save_tasks(self.downloads)
                    
                # 选择剧集并开始下载
                if not video.select_episode(episode_index):
                    with self.lock:
                        if task_id not in self.downloads:
                            return
                        self.downloads[task_id]['status'] = 'failed'
                        self.downloads[task_id]['error'] = '选择剧集失败'
                        self.task_store.save_tasks(self.downloads)
                        return
                        
                # 设置下载管理器引用
                downloader.set_download_manager(self)
                
                # 开始下载
                success = video.download(downloader, save_dir)
                
                with self.lock:
                    if task_id not in self.downloads:
                        return
                        
                    if success:
                        self.downloads[task_id]['status'] = 'completed'
                        self.downloads[task_id]['progress'] = 100
                        self.task_store.save_tasks(self.downloads)
                        return
                    elif retry_count == max_retries - 1:
                        # 如果是最后一次重试，标记为失败
                        self.downloads[task_id]['status'] = 'failed'
                        self.task_store.save_tasks(self.downloads)
                        return
                        
            except Exception as e:
                with self.lock:
                    if task_id not in self.downloads:
                        return
                    # 如果是最后一次重试，标记为失败
                    if retry_count == max_retries - 1:
                        self.downloads[task_id]['status'] = 'failed'
                        self.task_store.save_tasks(self.downloads)
                        return
            
            # 如果到这里，说明下载失败，准备重试
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(10)  # 等待10秒后重试
                with self.lock:
                    if task_id not in self.downloads:
                        return
                    self.downloads[task_id]['status'] = f'等待重试 ({retry_count}/{max_retries-1})'
                    self.task_store.save_tasks(self.downloads)

    def get_status(self):
        """获取所有下载任务的状态"""
        try:
            with self.lock:
                return {
                    task_id: {
                        'status': info['status'],
                        'progress': info['progress'],
                        'video': info['video'].title,
                        'episode': info['episode']['title'],
                        'save_dir': info['save_dir'],
                        'speed': info.get('speed', '-')
                    }
                    for task_id, info in self.downloads.items()
                }
        except Exception as e:
            console.print(f"[yellow]获取状态时出错: {str(e)}[/yellow]")
            return {}

    def print_status(self):
        """打印下载状态"""
        try:
            statuses = self.get_status()
            if not statuses:
                console.print("[yellow]暂无下载任务[/yellow]")
                return
                
            console.print("\n[bold green]下载任务状态[/bold green]")
            
            # 创建状态表格
            table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("序号", style="cyan", width=6)
            table.add_column("视频", style="white")
            table.add_column("剧集", style="white")
            table.add_column("状态", style="white")
            table.add_column("进度", style="white")
            table.add_column("速度", style="white")
            
            # 计算总下载速度
            total_speed = 0
            downloading_count = 0
            
            for i, (task_id, info) in enumerate(statuses.items(), 1):
                try:
                    status_style = {
                        'pending': '[yellow]等待中[/yellow]',
                        'downloading': '[blue]下载中[/blue]',
                        'completed': '[green]已完成[/green]',
                        'failed': '[red]失败[/red]'
                    }.get(info['status'], info['status'])
                    
                    # 获取进度
                    progress = "100%" if info['status'] == 'completed' else \
                              "0%" if info['status'] == 'pending' or info['status'] == 'failed' else \
                              f"{info.get('progress', 0):.1f}%"
                    
                    # 获取速度
                    speed = info.get('speed', '-')
                    
                    table.add_row(
                        str(i),
                        info['video'],
                        info['episode'],
                        status_style,
                        progress,
                        speed
                    )
                    
                    # 累计下载速度
                    if info['status'] == 'downloading':
                        downloading_count += 1
                        try:
                            speed_str = speed
                            if speed_str.endswith('MB/s'):
                                total_speed += float(speed_str[:-5]) * 1024 * 1024
                            elif speed_str.endswith('KB/s'):
                                total_speed += float(speed_str[:-5]) * 1024
                            elif speed_str.endswith('B/s'):
                                total_speed += float(speed_str[:-4])
                        except (ValueError, AttributeError):
                            pass
                            
                except Exception as e:
                    console.print(f"[yellow]处理任务 {task_id} 状态时出错: {str(e)}[/yellow]")
                    continue
            
            console.print(table)
            
            # 显示总下载速度
            if downloading_count > 0:
                try:
                    if total_speed > 1024 * 1024:
                        speed_str = f"{total_speed / (1024 * 1024):.2f} MB/s"
                    elif total_speed > 1024:
                        speed_str = f"{total_speed / 1024:.2f} KB/s"
                    else:
                        speed_str = f"{total_speed:.2f} B/s"
                    console.print(f"\n[bold blue]当前下载速度: {speed_str}[/bold blue]")
                except Exception as e:
                    console.print("[yellow]计算下载速度时出错[/yellow]")
            
            console.print()
            
        except Exception as e:
            console.print(f"[yellow]显示状态时出错: {str(e)}[/yellow]")
            
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
                      

def parse_episode_ranges(input_str, max_episodes):
    """解析剧集范围
    支持格式:
    - 单个数字: "1"
    - 逗号分隔: "1,2,3"
    - 范围: "1-3"
    - 合: "1-3,5,7-9"
    """
    result = set()
    try:
        # 按逗号分割，同时支持中文逗号
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
        raise ValueError("输入格式无效，请使用数字、逗号和连字符，例如: 1-3,5,7-9")

class TaskStore:
    """下载任务持久化存储"""
    def __init__(self, store_path="download_tasks.json"):
        self.store_path = store_path
        
    def save_tasks(self, downloads):
        """保存下载任务到文件"""
        try:
            tasks = {}
            for task_id, info in downloads.items():
                # 只保存未完成和失败的任务，完成的任务不保存
                if info['status'] != 'completed':
                    tasks[task_id] = {
                        'video_title': info['video'].title,
                        'video_url': info['video'].detail_url,
                        'episode_title': info['episode']['title'],
                        'episode_url': info['episode']['url'],
                        'save_dir': info['save_dir'],
                        'save_path': info['save_path'],
                        'status': info['status'],
                        'progress': info['progress'],
                        'created_at': info.get('created_at', datetime.now().isoformat())
                    }
            
            # 如果没有需要保存的任务，且文件存在，则删除文件
            if not tasks and os.path.exists(self.store_path):
                os.remove(self.store_path)
                return
                
            # 否则写入未完成和失败的任务
            with open(self.store_path, 'w', encoding='utf-8') as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            console.print(f"[red]保存任务失败: {str(e)}[/red]")
            
    def load_tasks(self):
        """从文件加载任务"""
        try:
            # 检查文件是否存在
            if not os.path.exists(self.store_path):
                return {}
                
            # 检查文件大小
            if os.path.getsize(self.store_path) == 0:
                os.remove(self.store_path)
                return {}
                
            try:
                with open(self.store_path, 'r', encoding='utf-8') as f:
                    tasks = json.load(f)
                    
                # 验证任务数据的完整性
                valid_tasks = {}
                for task_id, task_info in tasks.items():
                    required_fields = [
                        'video_title', 'video_url', 'episode_title', 'episode_url',
                        'save_dir', 'save_path', 'status', 'progress'
                    ]
                    
                    # 检查必需字段
                    if all(field in task_info for field in required_fields):
                        # 检查文件是否已完成
                        if os.path.exists(task_info['save_path']) and os.path.getsize(task_info['save_path']) > 0:
                            continue  # 跳过已完成的任务
                            
                        # 检查临时目录，但不创建
                        temp_dir = f"{task_info['save_path']}.downloading"
                        
                        # 保留原始状态和进度
                        valid_tasks[task_id] = task_info
                    else:
                        console.print(f"[yellow]跳过无效的任务记录: {task_id}[/yellow]")
                
                return valid_tasks
                
            except json.JSONDecodeError:
                console.print("[yellow]任务文件格式错误，将重新创建[/yellow]")
                os.remove(self.store_path)
                return {}
                
        except Exception as e:
            console.print(f"[red]加载任务失败: {str(e)}[/red]")
            return {}

def main():
    try:
        # 设置默认下载路径
        default_path = "downloads"
        default_save_dir = os.path.abspath(default_path)
        
        # 创建下载管理器
        download_manager = DownloadManager()
        # 使用默认的并行下载数
        downloader = MovieDownloader(max_workers=48)
        downloader.set_download_manager(download_manager)
        
        # 恢复未完成的下载任务
        restored_count = download_manager.restore_tasks(downloader)
        if restored_count > 0:
            console.print(f"\n[green]已恢复 {restored_count} 个未完成的下载任务[/green]")
            
        # 启动自动保存线程
        download_manager.start_auto_save()
        
        # 注册信号处理
        signal.signal(signal.SIGINT, downloader.stop_download)
        signal.signal(signal.SIGTERM, downloader.stop_download)

        # 添加状态监控函数
        def monitor_status():
            import threading
            import time
            
            stop_monitor = threading.Event()
            
            def status_update():
                while not stop_monitor.is_set():
                    console.clear()
                    console.print("\n[bold blue]下载任务状态 (按回车返回)[/bold blue]")
                    download_manager.print_status()
                    time.sleep(1)
            
            # 启动状态更新线程
            update_thread = threading.Thread(target=status_update)
            update_thread.daemon = True
            update_thread.start()
            
            # 等待用户输入
            input()
            stop_monitor.set()
            update_thread.join()
            console.print("\n[cyan]返回主界面[/cyan]")

        while True:  # 主搜索循环
            # 显示当前下载状态
            if download_manager.get_active_count() > 0:
                download_manager.print_status()
                
            # 修改提示文本
            keyword = input("\n[主界面] 请输入要搜索的视频名称（直接回车查看下载状态，输入 q 退出，输入 t 查看所有任务）: ")
            if not keyword:
                monitor_status()
                continue
            if keyword.lower() == 'q':
                if download_manager.get_active_count() > 0:
                    confirm = input("\n当前有正在下载的任务，确定要退出吗？(y/N): ")
                    if confirm.lower() != 'y':
                        continue
                # 停止下载管理器并保存最后的状态
                download_manager.stop()
                console.print("\n[green]程序已退出，未完成的下载将在下次运行时继续[/green]")
                os._exit(0)
            if keyword.lower() == 't':
                # 显示所有任务历史
                console.print("\n[bold green]所有下载任务:[/bold green]")
                table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
                table.add_column("序号", style="cyan", width=6)
                table.add_column("视频", style="white")
                table.add_column("剧集", style="white")
                table.add_column("状态", style="white")
                table.add_column("进度", style="white")
                table.add_column("创建时间", style="white")
                
                for i, (task_id, info) in enumerate(sorted(download_manager.downloads.items(), 
                    key=lambda x: x[1]['created_at'], reverse=True), 1):
                    status_style = {
                        'pending': '[yellow]等待中[/yellow]',
                        'downloading': '[blue]下载中[/blue]',
                        'completed': '[green]已完成[/green]',
                        'failed': '[red]失败[/red]'
                    }.get(info['status'], info['status'])
                    
                    created_time = datetime.fromisoformat(info['created_at']).strftime('%Y-%m-%d %H:%M:%S')
                    
                    table.add_row(
                        str(i),
                        info['video'].title,
                        info['episode']['title'],
                        status_style,
                        f"{info['progress']:.1f}%",
                        created_time
                    )
                
                console.print(table)
                input("\n按回车继续...")
                continue
                
            # 搜索视频
            videos = downloader.search_video(keyword)
            if not videos:
                console.print("[red]未找到相关视频，请尝试其他关键词[/red]")
                continue

            while True:  # 视频选择循环
                # 显示搜索结果
                console.print("\n[bold green]搜索结果:[/bold green]")
                table = Table(show_header=True, header_style="bold magenta")
                table.add_column("序号", style="cyan", width=6)
                table.add_column("片名", style="white")
                table.add_column("海报", style="blue")
                
                for i, video in enumerate(videos, 1):
                    poster_info = "[blue]📷[/blue] " + (video.poster if video.poster else "无海报")
                    table.add_row(str(i), video.title, poster_info)
                console.print(table)

                choice = input("\n[视频选择] 请输入要下载的视频编号（直接回车查看下载状态，输入b返回搜索）: ")
                if not choice:
                    monitor_status()
                    continue
                if choice.lower() == 'b':
                    break  # 返回搜索界面

                try:
                    choice = int(choice) - 1
                    if 0 <= choice < len(videos):
                        video = videos[choice]
                        video_info = downloader.get_movie_info(video.detail_url)
                        
                        if not video.get_episodes(downloader):
                            console.print("[red]获取剧集列表失败，请重试[/red]")
                            continue

                        while True:  # 剧集选择循环
                            # 显示影片信息
                            if video_info:
                                console.print("\n[bold yellow]影片信息:[/bold yellow]")
                                for key, label in [
                                    ('title', '片名'),
                                    ('score', '评分'),
                                    ('type', '类型'),
                                    ('area', '地区'),
                                    ('year', '年份'),
                                    ('director', '导演'),
                                    ('actors', '主演')
                                ]:
                                    if key in video_info:
                                        console.print(f"[bold]{label}:[/bold] {video_info[key]}")
                                if 'description' in video_info:
                                    console.print(f"\n[bold]剧情简介:[/bold]\n{video_info['description']}")

                            # 显示剧集列表
                            console.print(f"\n[bold green]剧集列表[/bold green] [blue](共{len(video.episodes)}集)[/blue]")
                            table = Table(box=box.ROUNDED)
                            
                            # 添加表头
                            table.add_column("序号", style="cyan", justify="center")
                            table.add_column("剧集", style="white", justify="left")
                            table.add_column("序号", style="cyan", justify="center")
                            table.add_column("剧集", style="white", justify="left")
                            table.add_column("序号", style="cyan", justify="center")
                            table.add_column("剧集", style="white", justify="left")
                            table.add_column("序号", style="cyan", justify="center")
                            table.add_column("剧集", style="white", justify="left")
                            
                            COLUMNS = 4  # 每行显示的剧集数
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
                            
                            for row in rows:
                                table.add_row(*row)
                            
                            console.print(table)
                            console.print("\n[cyan]提示: 支持范围选择，例如: 1-3,5,7-9[/cyan]")

                            ep_choice = input("\n[剧集选择] 请输入要下载的剧集编号（直接回车查看下载状态，输入b返回视频选择）: ")
                            if not ep_choice:
                                monitor_status()
                                continue
                            if ep_choice.lower() == 'b':
                                break  # 返回视频选择

                            try:
                                ep_choices = parse_episode_ranges(ep_choice, len(video.episodes))
                                save_dir = default_save_dir
                                
                                # 添加下载任务
                                download_success = False
                                added_tasks = []  # 记录添加的任务
                                
                                # 先检查所有任务是否已存在
                                existing_tasks = []
                                for ep_idx in ep_choices:
                                    task_id = f"{video.title}_{ep_idx}"
                                    if task_id in download_manager.downloads:
                                        existing_tasks.append(video.episodes[ep_idx]['title'])
                                
                                # 显示已存在的任务
                                if existing_tasks:
                                    console.print("\n[yellow]以下任务已存在:[/yellow]")
                                    for task in existing_tasks:
                                        console.print(f"[yellow]- {task}[/yellow]")
                                
                                # 添加新任务
                                for ep_idx in ep_choices:
                                    task_id = f"{video.title}_{ep_idx}"
                                    if task_id not in download_manager.downloads:
                                        if download_manager.add_download(video, ep_idx, save_dir, downloader):
                                            added_tasks.append(video.episodes[ep_idx]['title'])
                                            download_success = True
                                
                                if download_success:
                                    # 批量显示添加的任务
                                    if added_tasks:
                                        console.print("\n[green]已添加以下下载任务:[/green]")
                                        for task in added_tasks:
                                            console.print(f"[green]- {task}[/green]")
                                    
                                    # 不等待，直接显示状态并返回
                                    monitor_status()
                                    break  # 返回到搜索界面
                                
                            except ValueError as e:
                                console.print(f"[red]错误: {str(e)}[/red]")
                                continue
                            
                    else:
                        console.print("[red]无效的选择，请输入正确的编号[/red]")
                        
                except ValueError:
                    console.print("[red]请输入有效的数字[/red]")
                    continue

    except KeyboardInterrupt:
        console.print("\n[yellow]接收到退出信号[/yellow]")
        # 停止下载管理器并保存最后的状态
        download_manager.stop()
        console.print("\n[green]程序已退出，未完成的下载将在下次运行时继续[/green]")
        os._exit(0)

if __name__ == "__main__":
    main() 