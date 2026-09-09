import requests
import json
import re
from typing import Dict, List, Optional
import os
import pickle
import configparser
import platform


class InteractiveEpisodeRenamer:
    def __init__(self, base_url: str, username: str, password: str):
        """
        交互式剧集重命名工具
        
        :param base_url: OpenList服务的基础URL
        :param username: 用户名
        :param password: 密码
        """
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.token = None
        self.current_path = "/"
        if platform.system() == "Windows":
            ep_path = os.path.expandvars(os.environ.get("EPISODE_PATH", "%USERPROFILE%"))
        else:
            ep_path = os.environ.get("EPISODE_PATH", "/tmp")
        self.token_file_path = os.path.join(ep_path, "token")
        self.config_file_path = os.path.join(ep_path, "episode_renamer.conf")
        self.settings_file_path = os.path.join(ep_path, "episode_renamer.settings.json")
        self.settings = self.load_settings()



    def save_config(self, base_url: str):
        """
        保存配置到本地文件
        """
        try:
            config = configparser.ConfigParser()
            
            # 确保配置目录存在
            config_dir = os.path.dirname(self.config_file_path)
            os.makedirs(config_dir, exist_ok=True)
            
            # 设置配置值
            config['DEFAULT'] = {
                'base_url': base_url,
            }
            
            with open(self.config_file_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
            
            print(f"配置已保存到 {self.config_file_path}")
        except Exception as e:
            print(f"保存配置失败: {e}")

    def load_config(self) -> dict:
        """
        从本地文件加载配置
        """
        try:
            if os.path.exists(self.config_file_path):
                config = configparser.ConfigParser()
                config.read(self.config_file_path, encoding='utf-8')
                
                return {
                    'base_url': config.get('DEFAULT', 'base_url', fallback='http://127.0.0.1:5244')
                }
            else:
                return {
                    'base_url': 'http://127.0.0.1:5244'
                }
        except Exception as e:
            print(f"加载配置失败: {e}")
            return {
                'base_url': 'http://127.0.0.1:5244'
            }

    # ==================== 用户设置（JSON 持久化） ====================

    DEFAULT_SETTINGS = {
        "tmdb_api_key": "",
        "delimiter": ".",
        "use_episode_title": True,
        "default_season": "1",
        "default_episode_start": "1",
        "theme": "light",
    }

    def load_settings(self) -> dict:
        """加载用户设置，缺失字段用默认值补齐"""
        settings = dict(self.DEFAULT_SETTINGS)
        try:
            if os.path.exists(self.settings_file_path):
                with open(self.settings_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    settings.update(data)
        except Exception as e:
            print(f"加载设置失败，使用默认值: {e}")
        return settings

    def save_settings(self, settings: dict) -> dict:
        """合并并保存用户设置"""
        try:
            merged = dict(self.DEFAULT_SETTINGS)
            merged.update({k: v for k, v in settings.items() if k in self.DEFAULT_SETTINGS})
            os.makedirs(os.path.dirname(self.settings_file_path), exist_ok=True)
            with open(self.settings_file_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            self.settings = merged
            print(f"设置已保存到 {self.settings_file_path}")
        except Exception as e:
            print(f"保存设置失败: {e}")
        return self.settings

    # ==================== TMDB API（配置了 Key 时使用，否则回退网页抓取） ====================

    def tmdb_api_search(self, keyword: str, api_key: str, media_type: str = "tv") -> List[Dict]:
        """通过 TMDB REST API 搜索剧集或电影。

        ``media_type`` 只接受 ``tv`` 或 ``movie``，返回结果统一使用 ``name``
        字段，方便前端复用同一套选择和重命名流程。
        """
        if media_type not in {"tv", "movie"}:
            raise ValueError("media_type must be 'tv' or 'movie'")
        endpoint = "tv" if media_type == "tv" else "movie"
        res = requests.get(
            f"https://api.themoviedb.org/3/search/{endpoint}",
            params={"api_key": api_key, "query": keyword, "language": "zh-CN"},
            timeout=15
        )
        res.raise_for_status()
        results = res.json().get("results", [])
        return [{
            "id": r.get("id"),
            "name": r.get("name" if media_type == "tv" else "title", ""),
            "overview": (r.get("overview") or "")[:120],
            "year": (r.get("first_air_date" if media_type == "tv" else "release_date", "") or "")[:4],
            "poster": (f"https://image.tmdb.org/t/p/w185{r['poster_path']}" if r.get("poster_path") else ""),
            "media_type": media_type,
        } for r in results[:10]]

    def tmdb_api_season(self, tmdb_id: int, season: int, api_key: str) -> Dict:
        """通过 TMDB REST API 获取指定季的分集名称，返回 {集数: 名称}"""
        res = requests.get(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}",
            params={"api_key": api_key, "language": "zh-CN"},
            timeout=15
        )
        res.raise_for_status()
        eps = {}
        for e in res.json().get("episodes", []):
            if e.get("name"):
                eps[str(e.get("episode_number"))] = e["name"]
        return eps

    def validate_current_user(self) -> bool:
        """
        验证当前令牌是否属于当前用户
        """
        if not self.token:
            return False
            
        # 尝试获取用户信息来验证令牌的有效性
        try:
            headers = {
                "Authorization": self.token,
                "Content-Type": "application/json"
            }
            
            # 尝试获取用户信息
            user_info_url = f"{self.base_url}/api/me"
            response = requests.get(user_info_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            # 如果请求成功，则认为令牌有效
            if "code" in result and result["code"] == 200 and "data" in result:
                # 检查返回的用户名是否与当前输入的用户名一致
                if "username" in result["data"]:
                    returned_username = result["data"]["username"]
                    return returned_username == self.username
                # 如果返回的数据结构不同，可能包含用户ID或其他标识符
                elif "nick" in result["data"]:
                    returned_nick = result["data"]["nick"]
                    return returned_nick == self.username
                elif "name" in result["data"]:
                    returned_name = result["data"]["name"]
                    return returned_name == self.username
                # 如果没有用户名字段，至少验证令牌是有效的
                else:
                    return True
            return False
        except requests.exceptions.RequestException:
            # 如果 /api/me 不可用，尝试使用文件列表API作为备用验证方式
            try:
                headers = {
                    "Authorization": self.token,
                    "Content-Type": "application/json"
                }
                
                # 尝试访问根目录
                list_url = f"{self.base_url}/api/fs/list"
                payload = {"path": "/"}
                
                response = requests.post(list_url, json=payload, headers=headers, timeout=30)
                response.raise_for_status()
                
                result = response.json()
                # 如果请求成功(code为200)，则认为令牌有效
                # 但无法验证用户名，所以这里只验证令牌有效性
                return result.get("code") == 200
            except requests.exceptions.RequestException:
                return False

    def save_token(self):
        """
        将token保存到本地文件
        """
        try:
            # 确保目录存在
            token_dir = os.path.dirname(self.token_file_path)
            os.makedirs(token_dir, exist_ok=True)
            
            with open(self.token_file_path, 'wb') as f:
                pickle.dump(self.token, f)
            print(f"令牌已保存到 {self.token_file_path}")
        except Exception as e:
            print(f"保存令牌失败: {e}")

    def load_token(self) -> bool:
        """
        从本地文件加载token
        """
        try:
            if os.path.exists(self.token_file_path):
                with open(self.token_file_path, 'rb') as f:
                    self.token = pickle.load(f)
                print("从本地文件加载令牌成功")
                return True
            return False
        except Exception as e:
            print(f"加载令牌失败: {e}")
            return False

    def login(self) -> bool:
        """
        登录获取JWT令牌
        """
        login_url = f"{self.base_url}/api/auth/login"
        
        payload = {
            "username": self.username,
            "password": self.password
        }
        
        try:
            response = requests.post(login_url, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            if data.get("code") == 200:
                self.token = data["data"]["token"]
                print("登录成功，获取到JWT令牌")
                return True
            else:
                print(f"登录失败: {data.get('message')}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"登录请求失败: {e}")
            return False

    def get_directory_contents(self, path: str = "/") -> Optional[List[Dict]]:
        """
        获取目录内容
        """
        if not self.token:
            print("错误: 未登录，请先调用login方法")
            return None
            
        list_url = f"{self.base_url}/api/fs/list"
        
        payload = {
            "path": path
        }
        
        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(list_url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if result.get("code") == 200:
                return result.get("data", {}).get("content", [])
            else:
                print(f"获取目录内容失败: {result.get('message')}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"获取目录内容请求失败: {e}")
            return None

    def list_directories(self, path: str = "/") -> List[Dict]:
        """
        列出指定路径下的所有目录
        """
        contents = self.get_directory_contents(path)
        if not contents:
            return []
        
        directories = []
        for item in contents:
            if item.get('is_dir', False):
                directories.append(item)
        
        return directories

    def list_files(self, path: str = "/") -> List[Dict]:
        """
        列出指定路径下的所有文件
        """
        contents = self.get_directory_contents(path)
        if not contents:
            return []
        
        files = []
        for item in contents:
            if not item.get('is_dir', False):
                files.append(item)
        
        return files

    def batch_rename(self, src_dir: str, rename_mapping: Dict[str, str]) -> bool:
        """
        批量重命名文件
        
        :param src_dir: 源目录路径
        :param rename_mapping: 重命名映射字典，格式为 {原文件名: 新文件名}
        :return: 是否成功
        """
        if not self.token:
            print("错误: 未登录，请先调用login方法")
            return False
            
        rename_url = f"{self.base_url}/api/fs/batch_rename"
        
        # 构建重命名对象列表
        rename_objects = []
        for src_name, new_name in rename_mapping.items():
            rename_objects.append({
                "src_name": src_name,
                "new_name": new_name
            })
        
        payload = {
            "src_dir": src_dir,
            "rename_objects": rename_objects
        }
        
        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(rename_url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            if result.get("code") == 200:
                print(f"批量重命名成功完成")
                print(f"处理了 {len(rename_objects)} 个文件")
                return True
            else:
                print(f"批量重命名失败: {result.get('message')}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"批量重命名请求失败: {e}")
            return False

    def rename_single_item(self, path: str, new_name: str) -> bool:
        """
        重命名单个文件或文件夹
        
        :param path: 文件或文件夹的完整路径
        :param new_name: 新名称
        :return: 是否成功
        """
        if not self.token:
            print("错误: 未登录，请先调用login方法")
            return False
            
        rename_url = f"{self.base_url}/api/fs/rename"
        
        payload = {
            "path": path,
            "name": new_name
        }
        
        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(rename_url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if result.get("code") == 200:
                print(f"重命名成功: {os.path.basename(path)} -> {new_name}")
                return True
            else:
                print(f"重命名失败: {result.get('message')}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"重命名请求失败: {e}")
            return False

    def extract_episode_info(self, filename: str) -> Dict[str, str]:
        """
        从文件名中提取剧集信息
        
        :param filename: 原始文件名
        :return: 包含剧集信息的字典
        """
        # 常见的剧集文件名模式
        patterns = [
            r'(.+?)[\s._-]*S?(\d+)[\s._-]*E?(\d+)',  # S01E01 或 1x01 格式
            r'(.+?)[\s._-]*(\d+)[\s._-]*(\d{2})',    # 第1季第01集格式
            r'(.+?)[\s._-]*EP?[\s._-]*(\d+)',        # EP1 格式
            r'(.+?)[\s._-]*(\d+)[\s._-]*of[\s._-]*\d+',  # 1 of 10 格式
        ]
        
        for pattern in patterns:
            match = re.search(pattern, filename, re.IGNORECASE)
            if match:
                groups = match.groups()
                if len(groups) >= 2:
                    return {
                        'title': groups[0].strip(' ._-'),
                        'season': groups[1] if len(groups) > 1 else '1',
                        'episode': groups[2] if len(groups) > 2 else groups[1]
                    }
        
        # 如果没有匹配到模式，返回基本文件名信息
        name, ext = os.path.splitext(filename)
        return {
            'title': name,
            'season': '1',
            'episode': '1'
        }

    def generate_standard_name(self, episode_info: Dict[str, str], naming_pattern: str = "{title}.S{season}E{episode:02d}") -> str:
        """
        根据剧集信息和命名模式生成标准文件名
        
        :param episode_info: 剧集信息字典
        :param naming_pattern: 命名模式
        :return: 标准文件名
        """
        try:
            season = str(episode_info.get('season', '1')).zfill(2)
            episode = int(episode_info.get('episode', '1'))
            title = episode_info.get('title', 'Unknown').strip()
            episode_title = episode_info.get('episode_title', '').strip()
            
            # 清理标题中的特殊字符
            title = re.sub(r'[<>:"/\\|?*]', '_', title)
            if episode_title:
                episode_title = re.sub(r'[<>:"/\\|?*]', '_', episode_title)
            
            # 支持动态集名拼接
            format_dict = {
                'season': season,
                'episode': episode,
                'title': title,
                'episode_title': episode_title
            }
            return naming_pattern.format(**format_dict)
        except Exception as e:
            print(f"生成标准名称时出错: {e}")
            return episode_info.get('title', 'Unknown')
