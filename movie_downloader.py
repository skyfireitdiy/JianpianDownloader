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
import threading

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
        
    def download(self, downloader, save_dir="downloads"):
        """下载当前选中的剧集"""
        if not self.current_episode:
            print("请先选择要下载的剧集")
            return False
            
        save_path = f"{save_dir}/{re.sub(r'[<>:"/\\|?*]', '', self.title)}/{self.current_episode['title']}.mp4"
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
                print("未找到放列表")
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
        try:
            signal.signal(signal.SIGINT, self.stop_download)
            signal.signal(signal.SIGTERM, self.stop_download)
            
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
                self.console.print("[red]未找到视频片段[/red]")
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
                
                # 并行下载
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = [executor.submit(download_segment, args) for args in remaining_segments]
                    for future in concurrent.futures.as_completed(futures):
                        if self.stop_flag:
                            for f in futures:
                                f.cancel()
                            executor._threads.clear()
                            concurrent.futures.thread._threads_queues.clear()
                            break
                        
                        result = future.result()
                        if result:
                            index, success = result
                            if success:
                                success_count += 1
                                # 使用\r清除当前行，并使用空格填充
                                print(f"\r下载进度: {success_count/len(segments)*100:5.1f}% "
                                      f"({success_count:4d}/{len(segments)}) "
                                      f"- {speed_monitor.format_speed():>10}        ", 
                                      end='', flush=True)
                
                # 最后打印一个换行
                print()
                
                if self.stop_flag:
                    self.console.print("\n[yellow]下载已停止[/yellow]")
                    return False
                
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
                
                # 检查文件大小
                file_size = os.path.getsize(save_path)
                if file_size == 0:
                    self.console.print("[red]下载失败: 文件大小为0[/red]")
                    os.remove(save_path)
                    return False
                
                self.console.print(Panel(f"[green]下载完成: {save_path}\n文件大小: {file_size / (1024*1024):.2f} MB[/green]"))
                return True
                
        except Exception as e:
            if not self.stop_flag:
                self.console.print(f"[red]下载失败: {str(e)}[/red]")
            if os.path.exists(save_path):
                os.remove(save_path)
            return False
        finally:
            # 重置停止标志
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
                        # 处理转义的url
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

def main():
    # 获取并行下载数
    try:
        max_workers = int(input("请输入并行下载数量(默认48): ") or "48")
        if max_workers < 1:
            print("并行数量必须大于0使用默认值48")
            max_workers = 48
    except ValueError:
        print("输入无效，使用默认值48")
        max_workers = 48
        
    downloader = MovieDownloader(max_workers=max_workers)
    
    # 搜索视频
    keyword = input("请输入要搜索的视频名称: ")
    videos = downloader.search_video(keyword)
    
    if not videos:
        print("未找到相关视频")
        return
        
    # 显示搜索结果
    print("\n搜索结果:")
    for i, video in enumerate(videos, 1):
        poster_info = f"[海报: {video.poster}]" if video.poster else ""
        print(f"{i}. {video.title} {poster_info}")
        
    # 选择视频
    choice = int(input("\n请输入要下载的视频编号: ")) - 1
    if 0 <= choice < len(videos):
        video = videos[choice]
        
        # 获取并显示影片信息
        downloader.get_movie_info(video.detail_url)
        
        # 获取剧集列表
        if not video.get_episodes(downloader):
            print("获取剧集列表失败")
            return
            
        # 显示剧集列表
        print(f"\n剧集列表 (共{len(video.episodes)}集):")
        for i, ep in enumerate(video.episodes, 1):
            print(f"{i}. {ep['title']}")
            
        # 选择要下载的剧集
        ep_choice = input("\n请输入要下载的剧集编号(多个用逗号分隔): ")
        ep_choices = [int(x.strip()) - 1 for x in ep_choice.split(",")]
        
        # 下载中的剧集
        for ep_idx in ep_choices:
            if video.select_episode(ep_idx):
                print(f"\n开始下载: {video.current_episode['title']}")
                video.download(downloader)
            else:
                print(f"无效的剧集编号: {ep_idx + 1}")
    else:
        print("无效的选择")

if __name__ == "__main__":
    main() 