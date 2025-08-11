#!/usr/bin/env python3
"""
JAV Metadata Updater for Plex
自动为 Plex 中的 JAV 视频添加分类和元数据
"""

import re
import time
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import yaml
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound
import requests
from bs4 import BeautifulSoup
import cloudscraper
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import os
import tempfile

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('jav_meta_updater.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class JAVNumberExtractor:
    """提取 JAV 番号的工具类"""
    
    PATTERNS = [
        # 优先匹配标准JAV格式（2-5个字母+连字符+数字）
        r'([A-Z]{2,5})-(\d{3,5})',  # 标准格式: ABC-123
        r'([A-Z]{2,5})[-_](\d{3,5})',  # 带下划线: ABC_123
        r'([A-Z]{3,5})(\d{3,5})',  # 无分隔符: ABC123
        r'([A-Z]{2,5})\.(\d{3,5})',  # 带点号: ABC.123
        r'([A-Z]{1}[A-Z0-9]{2,4})-(\d{3,5})',  # 特殊格式: 1PON-123
        r'(\d{6})[-_](\d{3})',  # 纯数字格式: 012345-123
        r'([A-Z]{2,5})[-_]([A-Z]\d{2,4})',  # 特殊格式: ABC-A123
    ]
    
    @classmethod
    def extract(cls, filename: str) -> Optional[str]:
        """从文件名中提取番号"""
        # 获取完整路径用于搜索（转换为大写）
        full_path = str(filename).upper()
        
        # 策略1: 在路径中查找空格前的番号（适用于 "CJOD-160 title" 这种格式）
        # 分割路径，检查每个部分
        parts = re.split(r'[/\\]', full_path)  # 分割路径
        for part in parts:
            first_space_pos = part.find(' ')
            if first_space_pos > 0:
                potential_code = part[:first_space_pos].strip()
                for pattern in cls.PATTERNS:
                    match = re.match(f'^{pattern}$', potential_code)
                    if match:
                        prefix = match.group(1)
                        number = match.group(2)
                        return f"{prefix}-{number}"
        
        # 策略2: 获取最终文件名（不含扩展名）
        filename_only = Path(filename).stem.upper()
        first_space_pos = filename_only.find(' ')
        if first_space_pos > 0:
            potential_code = filename_only[:first_space_pos].strip()
            for pattern in cls.PATTERNS:
                match = re.match(f'^{pattern}$', potential_code)
                if match:
                    prefix = match.group(1)
                    number = match.group(2)
                    return f"{prefix}-{number}"
        
        # 策略3: 在整个路径中搜索任何符合格式的番号
        for pattern in cls.PATTERNS:
            match = re.search(pattern, full_path)
            if match:
                prefix = match.group(1)
                number = match.group(2)
                return f"{prefix}-{number}"
        
        return None


class JavLibraryScraper:
    """JavLibrary 爬虫类"""
    
    def __init__(self, base_url: str = "https://www.javlibrary.com", 
                 proxy: Optional[str] = None, 
                 timeout: int = 10,
                 language: str = "cn",
                 cookies: Optional[str] = None,
                 user_agent: Optional[str] = None,
                 rate_limit: float = 1.0,
                 max_retries: int = 3):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.language = language  # cn, en, ja
        self.rate_limit = rate_limit  # 请求间隔（秒）
        self.max_retries = max_retries  # 最大重试次数
        self.last_request_time = 0  # 上次请求时间
        self.consecutive_429_count = 0  # 连续429错误计数
        self.adaptive_delay = 0  # 自适应延迟
        self.scraper = cloudscraper.create_scraper()
        
        if proxy:
            self.scraper.proxies = {
                'http': proxy,
                'https': proxy
            }
        
        # 设置 User-Agent
        if user_agent:
            self.user_agent = user_agent
        else:
            self.user_agent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        
        self.headers = {
            'User-Agent': self.user_agent,
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        # 处理 Cookie
        if cookies:
            self.headers['Cookie'] = cookies
            # 同时设置到 scraper 的 cookies
            self._parse_cookies(cookies)
    
    def _parse_cookies(self, cookie_string: str):
        """解析 Cookie 字符串并设置到 scraper"""
        cookies = {}
        for item in cookie_string.split('; '):
            if '=' in item:
                key, value = item.split('=', 1)
                cookies[key] = value
        
        for key, value in cookies.items():
            self.scraper.cookies.set(key, value)
    
    def _rate_limited_request(self, method: str, url: str, **kwargs):
        """带有频率限制和重试机制的请求"""
        # 确保请求间隔（包含自适应延迟）
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        total_delay = self.rate_limit + self.adaptive_delay
        
        if time_since_last < total_delay:
            sleep_time = total_delay - time_since_last
            logger.debug(f"访问频率限制：等待 {sleep_time:.2f} 秒 (基础:{self.rate_limit}s + 自适应:{self.adaptive_delay}s)")
            time.sleep(sleep_time)
        
        # 重试机制
        for attempt in range(self.max_retries):
            try:
                self.last_request_time = time.time()
                response = getattr(self.scraper, method.lower())(url, **kwargs)
                
                # 检查响应状态
                if response.status_code == 403:
                    logger.warning(f"访问被拒绝 (403)，尝试 {attempt + 1}/{self.max_retries}")
                    if attempt < self.max_retries - 1:
                        # 指数退避
                        wait_time = (2 ** attempt) * self.rate_limit
                        logger.info(f"等待 {wait_time:.2f} 秒后重试")
                        time.sleep(wait_time)
                        continue
                elif response.status_code == 429:
                    self.consecutive_429_count += 1
                    logger.warning(f"请求频率过快 (429)，尝试 {attempt + 1}/{self.max_retries}，连续429次数: {self.consecutive_429_count}")
                    
                    # 自适应调整：如果连续遇到429，增加基础延迟
                    if self.consecutive_429_count >= 3:
                        self.adaptive_delay = min(self.adaptive_delay + 1.0, 5.0)  # 最多增加5秒
                        logger.info(f"🐌 自适应减速: 增加 {self.adaptive_delay:.1f}s 延迟")
                    
                    if attempt < self.max_retries - 1:
                        # 429错误使用渐进式退避：5秒 -> 15秒 -> 30秒
                        wait_times = [5.0, 15.0, 30.0]
                        wait_time = wait_times[min(attempt, len(wait_times)-1)]
                        logger.info(f"等待 {wait_time:.2f} 秒后重试")
                        time.sleep(wait_time)
                        continue
                
                # 成功请求，重置429计数器
                if response.status_code == 200:
                    if self.consecutive_429_count > 0:
                        logger.debug(f"✅ 请求成功，重置429计数器")
                    self.consecutive_429_count = 0
                    # 逐渐减少自适应延迟
                    if self.adaptive_delay > 0:
                        self.adaptive_delay = max(0, self.adaptive_delay - 0.5)
                
                return response
                
            except Exception as e:
                logger.warning(f"请求失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    wait_time = (2 ** attempt) * self.rate_limit
                    time.sleep(wait_time)
                else:
                    raise
        
        return None
    
    def search_by_code(self, code: str) -> Optional[Dict]:
        """根据番号搜索影片信息"""
        try:
            # 根据语言设置构建URL
            lang_path = f"/{self.language}" if self.language != "en" else ""
            search_url = f"{self.base_url}{lang_path}/vl_searchbyid.php"
            params = {'keyword': code}
            
            response = self._rate_limited_request(
                'get',
                search_url, 
                params=params, 
                headers=self.headers,
                timeout=self.timeout
            )
            
            if not response or response.status_code != 200:
                logger.warning(f"搜索 {code} 失败: HTTP {response.status_code if response else 'None'}")
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 调试：记录响应状态和URL
            logger.debug(f"响应状态: {response.status_code}, URL: {response.url}")
            logger.debug(f"Content-Type: {response.headers.get('Content-Type', 'N/A')}")
            
            # 检查是否直接跳转到详情页
            if 'vl_searchbyid.php' not in response.url:
                return self._parse_detail_page(soup, code)
            
            # 搜索结果页，找到第一个匹配的链接
            video_links = soup.find_all('a', href=re.compile(r'\?v='))
            for link in video_links:
                if code.upper() in link.text.upper():
                    detail_url = self.base_url + lang_path + '/' + link['href']
                    return self._fetch_detail(detail_url, code)
            
            logger.warning(f"未找到番号 {code} 的信息")
            return None
            
        except Exception as e:
            logger.error(f"搜索 {code} 时出错: {e}")
            return None
    
    def _fetch_detail(self, url: str, code: str) -> Optional[Dict]:
        """获取详情页信息"""
        try:
            response = self._rate_limited_request('get', url, headers=self.headers, timeout=self.timeout)
            if not response or response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            return self._parse_detail_page(soup, code)
            
        except Exception as e:
            logger.error(f"获取详情页 {url} 失败: {e}")
            return None
    
    def _parse_detail_page(self, soup: BeautifulSoup, code: str) -> Dict:
        """解析详情页"""
        metadata = {
            'code': code,
            'title': '',
            'genres': [],
            'actors': [],
            'studio': '',
            'director': '',
            'release_date': '',
            'rating': 0,
            'cover_url': ''
        }
        
        try:
            # 调试：打印页面部分内容
            page_title = soup.find('title')
            if page_title:
                logger.debug(f"页面标题: {page_title.text.strip()}")
            
            # 标题 - 多种可能的选择器
            title_selectors = [
                'h3.post-title',
                'h3',
                'div.video h3',
                'div#video_title h3'
            ]
            
            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem:
                    metadata['title'] = title_elem.text.strip()
                    logger.debug(f"找到标题: {metadata['title']}")
                    break
            
            # 封面图 - 多种可能的选择器
            img_selectors = [
                'img#video_jacket_img',
                'img.video-jacket',
                'div#video_jacket img'
            ]
            
            for selector in img_selectors:
                img_elem = soup.select_one(selector)
                if img_elem and img_elem.get('src'):
                    src = img_elem['src']
                    if src.startswith('//'):
                        metadata['cover_url'] = 'https:' + src
                    elif src.startswith('/'):
                        metadata['cover_url'] = self.base_url + src
                    else:
                        metadata['cover_url'] = src
                    logger.debug(f"找到封面: {metadata['cover_url']}")
                    break
            
            # 类别/标签 - 中文版网站会直接返回中文
            genre_selectors = [
                'div#video_genres a.genre',
                'div.genre a',
                'span.genre a'
            ]
            
            for selector in genre_selectors:
                genre_links = soup.select(selector)
                if genre_links:
                    metadata['genres'] = [g.text.strip() for g in genre_links]
                    logger.debug(f"获取到的类别: {metadata['genres']}")
                    break
            
            # 演员
            cast_selectors = [
                'div#video_cast a[href*="vl_star.php"]',
                'div.cast a[href*="vl_star.php"]',
                'span.star a'
            ]
            
            for selector in cast_selectors:
                actor_links = soup.select(selector)
                if actor_links:
                    metadata['actors'] = [a.text.strip() for a in actor_links]
                    logger.debug(f"找到演员: {metadata['actors']}")
                    break
            
            # 制作商
            maker_selectors = [
                'a[href*="vl_maker.php"]',
                'div.maker a'
            ]
            
            for selector in maker_selectors:
                maker_elem = soup.select_one(selector)
                if maker_elem:
                    metadata['studio'] = maker_elem.text.strip()
                    logger.debug(f"找到制作商: {metadata['studio']}")
                    break
            
            # 发行商（备选）
            if not metadata['studio']:
                label_selectors = [
                    'a[href*="vl_label.php"]',
                    'div.label a'
                ]
                
                for selector in label_selectors:
                    label_elem = soup.select_one(selector)
                    if label_elem:
                        metadata['studio'] = label_elem.text.strip()
                        logger.debug(f"找到发行商: {metadata['studio']}")
                        break
            
            # 发行日期 - 在详情表格中查找
            info_selectors = [
                'div#video_info',
                'div.info',
                'table.info'
            ]
            
            for selector in info_selectors:
                info_div = soup.select_one(selector)
                if info_div:
                    # 查找日期格式
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', info_div.text)
                    if date_match:
                        metadata['release_date'] = date_match.group(1)
                        logger.debug(f"找到发行日期: {metadata['release_date']}")
                        break
            
            # 评分
            rating_selectors = [
                'span.score',
                'div.score',
                'span.rating'
            ]
            
            for selector in rating_selectors:
                rating_elem = soup.select_one(selector)
                if rating_elem:
                    try:
                        rating_text = rating_elem.text.strip('()')
                        metadata['rating'] = float(rating_text)
                        logger.debug(f"找到评分: {metadata['rating']}")
                        break
                    except:
                        pass
            
            # 如果没有找到任何信息，保存页面HTML用于调试
            if not any([metadata['title'], metadata['genres'], metadata['actors']]):
                logger.debug("未找到主要信息，页面可能结构不同")
                # 打印页面的主要 div 结构
                main_divs = soup.find_all('div', id=True)[:10]
                logger.debug(f"页面主要 div IDs: {[div.get('id') for div in main_divs]}")
                
                # 保存页面HTML用于分析
                try:
                    with open(f'debug_page_{code}.html', 'w', encoding='utf-8') as f:
                        f.write(str(soup.prettify()))
                    logger.debug(f"页面HTML已保存到 debug_page_{code}.html")
                except:
                    pass
                
        except Exception as e:
            logger.error(f"解析页面失败: {e}")
        
        return metadata


class PlexJAVUpdater:
    """Plex JAV 元数据更新器"""
    
    def __init__(self, plex_url: str, plex_token: str, library_name: str, rules: Dict = None):
        self.plex = PlexServer(plex_url, plex_token)
        self.library = self.plex.library.section(library_name)
        self.scraper = None
        self.genre_mapping = {}
        self.collection_mapping = {}
        self.rules = rules or {}
    
    def set_scraper(self, scraper: JavLibraryScraper):
        """设置爬虫实例"""
        self.scraper = scraper
    
    def set_mappings(self, genre_mapping: Dict, collection_mapping: Dict):
        """设置分类映射"""
        self.genre_mapping = genre_mapping
        self.collection_mapping = collection_mapping
    
    def _download_cover(self, cover_url: str, video_title: str) -> Optional[str]:
        """下载封面图片并返回临时文件路径"""
        try:
            if not cover_url:
                return None
            
            # 创建临时目录
            temp_dir = Path(tempfile.gettempdir()) / "jav_covers"
            temp_dir.mkdir(exist_ok=True)
            
            # 生成文件名（使用视频标题，避免特殊字符）
            safe_title = re.sub(r'[^\w\-_\.]', '_', video_title)
            temp_file = temp_dir / f"{safe_title}.jpg"
            
            # 如果已经下载过，直接返回
            if temp_file.exists():
                return str(temp_file)
            
            # 下载图片
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.javlibrary.com/'
            }
            
            response = requests.get(cover_url, headers=headers, timeout=30)
            if response.status_code == 200:
                with open(temp_file, 'wb') as f:
                    f.write(response.content)
                logger.info(f"封面下载成功: {temp_file.name}")
                return str(temp_file)
            else:
                logger.warning(f"封面下载失败: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"下载封面失败: {e}")
            return None
    
    def _set_video_poster(self, video, cover_path: str) -> bool:
        """设置视频封面"""
        try:
            if not cover_path or not os.path.exists(cover_path):
                logger.warning("封面文件路径无效")
                return False
            
            # 方法1: 尝试直接用 URL 上传
            if hasattr(video, 'uploadPoster'):
                video.uploadPoster(filepath=cover_path)
                logger.debug(f"使用 uploadPoster(filepath) 成功")
                return True
                
        except Exception as e:
            logger.debug(f"uploadPoster(filepath) 失败: {e}")
            
            # 方法2: 尝试用 URL 方式上传
            try:
                # 先尝试从原始 URL 直接上传
                cover_url = getattr(self, '_last_cover_url', None)
                if cover_url and hasattr(video, 'uploadPoster'):
                    video.uploadPoster(url=cover_url)
                    logger.debug(f"使用 uploadPoster(url) 成功")
                    return True
            except Exception as e2:
                logger.debug(f"uploadPoster(url) 失败: {e2}")
            
            logger.error(f"所有封面上传方法都失败: 最后错误 {e}")
            return False
    
    def get_all_videos(self) -> List:
        """获取库中所有视频"""
        return self.library.all()
    
    def update_video_metadata(self, video, metadata: Dict) -> bool:
        """更新单个视频的元数据"""
        try:
            # 开始批量编辑
            video.batchEdits()
            
            # 更新标题（如果原标题只是文件名）
            if metadata['title'] and not video.title or video.title == Path(video.media[0].parts[0].file).stem:
                video.editTitle(metadata['title'])
            
            # 更新类别
            if metadata['genres']:
                # 智能映射：检测是否需要映射（只在获取到英文时才映射）
                genres_to_add = []
                collections_to_add = []
                
                for genre in metadata['genres']:
                    # 检查是否包含中文字符（如果已经是中文就不需要映射）
                    if any('\u4e00' <= char <= '\u9fff' for char in genre):
                        # 已经是中文，直接使用
                        mapped_genre = genre
                    else:
                        # 英文，需要映射
                        mapped_genre = self.genre_mapping.get(genre, genre)
                    
                    if mapped_genre:
                        genres_to_add.append(mapped_genre)
                    
                    # 不再使用基于类别的合集映射
                    pass
                
                # 添加类别
                if genres_to_add:
                    video.addGenre(genres_to_add)
                    logger.info(f"添加类别: {', '.join(genres_to_add)}")
            
            # 创建新的合集（基于番号和演员）
            collections_to_add = []
            
            # 1. 番号前缀合集
            if metadata['code']:
                code_prefix = metadata['code'].split('-')[0] if '-' in metadata['code'] else metadata['code'][:3]
                collections_to_add.append(f"{code_prefix}系列")
                logger.info(f"准备添加到系列合集: {code_prefix}系列")
            
            # 2. 主演员合集（第一个演员）
            if metadata.get('actors') and len(metadata['actors']) > 0:
                main_actor = metadata['actors'][0]
                collections_to_add.append(f"{main_actor}作品集")
                logger.info(f"准备添加到演员合集: {main_actor}作品集")
            
            # 3. 制作商合集（可选）
            if metadata.get('studio') and self.rules.get('add_studio_collection', False):
                collections_to_add.append(metadata['studio'])
                logger.info(f"准备添加到制作商合集: {metadata['studio']}")
            
            # 添加所有合集
            if collections_to_add:
                # 去重
                collections_to_add = list(set(collections_to_add))
                video.addCollection(collections_to_add)
                logger.info(f"✅ 添加到合集: {', '.join(collections_to_add)}")
            
            # 更新工作室
            if metadata['studio']:
                video.editStudio(metadata['studio'])
            
            # 更新演员（使用正确的Plex API方法）
            if metadata.get('actors'):
                logger.info(f"开始处理 {len(metadata['actors'])} 个演员")
                actors_to_add = metadata['actors'][:5]  # 限制前5个演员
                
                try:
                    # 方法1: 使用 edit() 方法添加演员 (推荐)
                    edits = {}
                    for i, actor in enumerate(actors_to_add):
                        edits[f'actor[{i}].tag.tag'] = actor
                        edits[f'actor[{i}].locked'] = 1
                        edits[f'actor[{i}].tagging.text'] = ''  # 角色名为空
                    
                    video.edit(**edits)
                    logger.info(f"✅ 批量添加演员成功: {', '.join(actors_to_add)}")
                    
                except Exception as e1:
                    logger.debug(f"批量添加演员失败: {e1}")
                    
                    try:
                        # 方法2: 使用 _edit_tags 方法
                        for actor in actors_to_add:
                            video._edit_tags(tag="actor", items=[actor])
                        logger.info(f"✅ 演员添加成功(_edit_tags): {', '.join(actors_to_add)}")
                        
                    except Exception as e2:
                        logger.debug(f"_edit_tags 方法失败: {e2}")
                        
                        # 方法3: 降级为标签
                        for actor in actors_to_add:
                            video.addLabel(f"演员:{actor}")
                        logger.info(f"📋 演员作为标签添加: {', '.join(actors_to_add)}")
                
                # 添加演员汇总标签（方便搜索）
                actors_tag = f"演员: {', '.join(metadata['actors'][:3])}"
                video.addLabel(actors_tag)
                logger.info(f"📋 演员汇总标签: {actors_tag}")
            else:
                logger.debug("没有演员信息")
            
            # 添加番号作为标签
            if metadata['code']:
                video.addLabel(metadata['code'])
            
            # 更新评分
            if metadata['rating'] > 0:
                try:
                    video.editRating(metadata['rating'])
                except AttributeError:
                    # 某些版本的 plexapi 可能不支持 editRating
                    logger.debug(f"跳过评分设置（API 不支持）: {metadata['rating']}")
                    pass
            
            # 下载并设置封面
            if metadata.get('cover_url') and self.rules.get('download_covers', True):
                cover_url = metadata['cover_url']
                logger.info(f"开始处理封面: {cover_url[:50]}...")
                
                # 保存cover_url供后续使用
                self._last_cover_url = cover_url
                
                # 更准确的封面检测
                has_poster = False
                try:
                    # 先刷新视频对象以获取最新状态
                    video.reload()
                    
                    # 检查是否有自定义封面
                    has_poster = (
                        (hasattr(video, 'thumb') and video.thumb and 
                         video.thumb.strip() and 'upload://' in video.thumb) or
                        (hasattr(video, 'art') and video.art and 
                         video.art.strip() and 'upload://' in video.art)
                    )
                    logger.debug(f"封面检测结果: {has_poster}")
                    if hasattr(video, 'thumb'):
                        logger.debug(f"当前thumb: {video.thumb}")
                        
                except Exception as e:
                    logger.debug(f"封面检测异常: {e}")
                    has_poster = False
                
                # 决定是否下载封面
                should_download = not has_poster or self.rules.get('overwrite_posters', False)
                
                if should_download:
                    # 方法1: 直接从URL上传（更高效）
                    try:
                        if hasattr(video, 'uploadPoster'):
                            video.uploadPoster(url=cover_url)
                            logger.info(f"✅ 封面设置成功(直接URL): {video.title}")
                        else:
                            raise Exception("uploadPoster 方法不存在")
                    except Exception as e1:
                        logger.debug(f"直接URL上传失败: {e1}")
                        
                        # 方法2: 下载后上传
                        cover_path = self._download_cover(cover_url, video.title)
                        if cover_path:
                            if self._set_video_poster(video, cover_path):
                                logger.info(f"✅ 封面设置成功(下载后): {video.title}")
                            else:
                                logger.warning(f"❌ 封面设置失败: {video.title}")
                        else:
                            logger.warning(f"❌ 封面下载失败: {video.title}")
                else:
                    logger.info(f"⏭️ 跳过封面（已存在且不覆盖）: {video.title}")
                    
            elif not metadata.get('cover_url'):
                logger.debug("没有封面URL")
            elif not self.rules.get('download_covers', True):
                logger.debug("封面下载功能已禁用")
            
            # 保存所有编辑
            video.saveEdits()
            
            logger.info(f"成功更新 {video.title} 的元数据")
            return True
            
        except Exception as e:
            logger.error(f"更新 {video.title} 失败: {e}")
            return False
    
    def process_video(self, video) -> Tuple[str, bool, Optional[Dict]]:
        """处理单个视频"""
        filename = Path(video.media[0].parts[0].file).name
        
        # 提取番号
        jav_code = JAVNumberExtractor.extract(filename)
        if not jav_code:
            logger.warning(f"无法从 {filename} 提取番号")
            return filename, False, None
        
        logger.info(f"处理: {filename} -> 番号: {jav_code}")
        
        # 检查是否已有完整信息（避免重复请求JavLibrary）
        has_genres = len(video.genres) > 0
        has_actors = hasattr(video, 'roles') and len(video.roles) > 0
        has_studio = hasattr(video, 'studio') and video.studio
        has_collections = len(video.collections) > 0
        
        # 如果已有基本信息（类别和演员/制作商），但没有合集，只创建合集
        if has_genres and (has_actors or has_studio) and not has_collections:
            logger.info(f"⚡ 已有元数据，仅创建合集: {jav_code}")
            
            # 创建番号前缀合集
            code_prefix = jav_code.split('-')[0] if '-' in jav_code else jav_code[:3]
            video.addCollection(f"{code_prefix}系列")
            logger.info(f"✅ 添加到系列合集: {code_prefix}系列")
            
            # 如果有演员，创建演员合集
            if has_actors:
                try:
                    main_actor = video.roles[0].tag if video.roles else None
                    if main_actor:
                        video.addCollection(f"{main_actor}作品集")
                        logger.info(f"✅ 添加到演员合集: {main_actor}作品集")
                except:
                    pass
            
            return filename, True, {"code": jav_code, "action": "仅更新合集"}
        
        # 如果已有完整信息（包括合集），跳过处理
        if has_genres and has_collections:
            logger.info(f"⚡ 跳过已处理的视频: {jav_code}")
            return filename, True, {"code": jav_code, "action": "跳过已处理"}
        
        # 需要获取元数据
        if not self.scraper:
            logger.error("未设置爬虫实例")
            return filename, False, None
        
        metadata = self.scraper.search_by_code(jav_code)
        if not metadata:
            logger.warning(f"未找到 {jav_code} 的元数据")
            return filename, False, None
        
        # 调试：输出获取到的元数据
        logger.debug(f"获取到的元数据: 演员={len(metadata.get('actors', []))}个, 封面={'有' if metadata.get('cover_url') else '无'}")
        if metadata.get('actors'):
            logger.info(f"演员列表: {', '.join(metadata['actors'][:3])}")
        if metadata.get('cover_url'):
            logger.info(f"封面URL: {metadata['cover_url'][:50]}...")
        
        # 更新 Plex
        success = self.update_video_metadata(video, metadata)
        
        return filename, success, metadata


def load_config(config_path: str) -> Dict:
    """加载配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description='JAV Metadata Updater for Plex')
    parser.add_argument('--config', default='config.yaml', help='配置文件路径')
    parser.add_argument('--limit', type=int, help='限制处理的视频数量')
    parser.add_argument('--code', help='只处理指定番号')
    parser.add_argument('--dry-run', action='store_true', help='测试模式，不实际更新')
    parser.add_argument('--threads', type=int, default=2, help='并发线程数')
    
    args = parser.parse_args()
    
    # 加载配置
    config = load_config(args.config)
    
    # 初始化爬虫
    scraper = JavLibraryScraper(
        base_url=config.get('javlibrary', {}).get('base_url', 'https://www.javlibrary.com'),
        proxy=config.get('javlibrary', {}).get('proxy'),
        timeout=config.get('javlibrary', {}).get('timeout', 10),
        language=config.get('javlibrary', {}).get('language', 'cn'),  # 默认使用中文
        cookies=config.get('javlibrary', {}).get('cookies'),
        user_agent=config.get('javlibrary', {}).get('user_agent'),
        rate_limit=config.get('javlibrary', {}).get('rate_limit', 1.0),  # 请求间隔
        max_retries=config.get('javlibrary', {}).get('max_retries', 3)  # 最大重试次数
    )
    
    # 初始化 Plex 更新器
    updater = PlexJAVUpdater(
        plex_url=config['plex']['url'],
        plex_token=config['plex']['token'],
        library_name=config['plex']['library'],
        rules=config.get('rules', {})
    )
    updater.set_scraper(scraper)
    updater.set_mappings(
        genre_mapping=config.get('genre_mapping', {}),
        collection_mapping=config.get('collection_mapping', {})
    )
    
    # 获取所有视频
    videos = updater.get_all_videos()
    
    if args.limit:
        videos = videos[:args.limit]
    
    if args.code:
        # 只处理特定番号
        target_videos = []
        for video in videos:
            filename = Path(video.media[0].parts[0].file).name
            if args.code.upper() in filename.upper():
                target_videos.append(video)
        videos = target_videos
    
    logger.info(f"找到 {len(videos)} 个视频待处理")
    
    if args.dry_run:
        logger.info("测试模式：只获取元数据，不更新 Plex")
    
    # 处理视频
    success_count = 0
    failed_count = 0
    results = []
    
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = {}
        
        for video in videos:
            if not args.dry_run:
                future = executor.submit(updater.process_video, video)
                futures[future] = video
            else:
                # 测试模式：只获取元数据
                filename = Path(video.media[0].parts[0].file).name
                jav_code = JAVNumberExtractor.extract(filename)
                if jav_code:
                    future = executor.submit(scraper.search_by_code, jav_code)
                    futures[future] = (filename, jav_code)
        
        # 使用进度条
        with tqdm(total=len(futures), desc="处理进度") as pbar:
            for future in as_completed(futures):
                try:
                    if not args.dry_run:
                        filename, success, metadata = future.result()
                        if success:
                            success_count += 1
                            results.append(f"✓ {filename}")
                        else:
                            failed_count += 1
                            results.append(f"✗ {filename}")
                    else:
                        metadata = future.result()
                        filename, jav_code = futures[future]
                        if metadata:
                            success_count += 1
                            logger.info(f"找到 {jav_code}: {metadata['title']}, 类别: {', '.join(metadata['genres'])}")
                        else:
                            failed_count += 1
                            logger.warning(f"未找到 {jav_code} 的信息")
                    
                except Exception as e:
                    failed_count += 1
                    logger.error(f"处理失败: {e}")
                
                pbar.update(1)
                
                # 只在实际请求JavLibrary时才延迟（跳过的视频不需要延迟）
                if metadata and not (isinstance(metadata, dict) and metadata.get('action') in ['仅更新合集', '跳过已处理']):
                    sleep_time = config.get('javlibrary', {}).get('rate_limit', 3.0)
                    time.sleep(sleep_time)
    
    # 输出统计
    logger.info("=" * 50)
    logger.info(f"处理完成！成功: {success_count}, 失败: {failed_count}")
    
    if results and not args.dry_run:
        logger.info("\n处理结果:")
        for result in results:
            logger.info(result)


if __name__ == "__main__":
    main()