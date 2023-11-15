from .jm_client_interface import *


# 抽象基类，实现了域名管理，发请求，重试机制，log，缓存等功能
class AbstractJmClient(
    JmcomicClient,
    PostmanProxy,
):
    client_key = '__just_for_placeholder_do_not_use_me__'
    func_to_cache = []

    def __init__(self,
                 postman: Postman,
                 retry_times: int,
                 domain=None,
                 fallback_domain_list=None,
                 ):
        super().__init__(postman)
        self.retry_times = retry_times

        if fallback_domain_list is None:
            fallback_domain_list = []

        if domain is not None:
            fallback_domain_list.insert(0, domain)

        self.domain_list = fallback_domain_list
        self.CLIENT_CACHE = None
        self.enable_cache()
        self.after_init()

    def after_init(self):
        pass

    def get(self, url, **kwargs):
        return self.request_with_retry(self.postman.get, url, **kwargs)

    def post(self, url, **kwargs):
        return self.request_with_retry(self.postman.post, url, **kwargs)

    def of_api_url(self, api_path, domain):
        return JmcomicText.format_url(api_path, domain)

    def get_jm_image(self, img_url) -> JmImageResp:

        def judge(resp):
            """
            使用此方法包装 self.get，使得图片数据为空时，判定为请求失败时，走重试逻辑
            """
            resp = JmImageResp(resp)
            resp.require_success()
            return resp

        return self.get(img_url, judge=judge)

    def request_with_retry(self,
                           request,
                           url,
                           domain_index=0,
                           retry_count=0,
                           judge=lambda resp: resp,
                           **kwargs,
                           ):
        """
        统一请求，支持重试
        :param request: 请求方法
        :param url: 图片url / path (/album/xxx)
        :param domain_index: 域名下标
        :param retry_count: 重试次数
        :param judge: 判定响应是否成功
        :param kwargs: 请求方法的kwargs
        """
        if domain_index >= len(self.domain_list):
            self.fallback(request, url, domain_index, retry_count, **kwargs)

        if url.startswith('/'):
            # path → url
            url = self.of_api_url(
                api_path=url,
                domain=self.domain_list[domain_index],
            )
            jm_log(self.log_topic(), self.decode(url))
        else:
            # 图片url
            pass

        if domain_index != 0 or retry_count != 0:
            jm_log(f'req.retry',
                     ', '.join([
                         f'次数: [{retry_count}/{self.retry_times}]',
                         f'域名: [{domain_index} of {self.domain_list}]',
                         f'路径: [{url}]',
                         f'参数: [{kwargs if "login" not in url else "#login_form#"}]'
                     ])
                   )

        try:
            resp = request(url, **kwargs)
            return judge(resp)
        except Exception as e:
            self.before_retry(e, kwargs, retry_count, url)

        if retry_count < self.retry_times:
            return self.request_with_retry(request, url, domain_index, retry_count + 1, judge, **kwargs)
        else:
            return self.request_with_retry(request, url, domain_index + 1, 0, judge, **kwargs)

    # noinspection PyMethodMayBeStatic
    def log_topic(self):
        return self.client_key

    # noinspection PyMethodMayBeStatic, PyUnusedLocal
    def before_retry(self, e, kwargs, retry_count, url):
        jm_log('req.error', str(e))

    def enable_cache(self):
        # noinspection PyDefaultArgument,PyShadowingBuiltins
        def make_key(args, kwds, typed,
                     kwd_mark=(object(),),
                     fasttypes={int, str},
                     tuple=tuple, type=type, len=len):
            key = args
            if kwds:
                key += kwd_mark
                for item in kwds.items():
                    key += item
            if typed:
                key += tuple(type(v) for v in args)
                if kwds:
                    key += tuple(type(v) for v in kwds.values())
            elif len(key) == 1 and type(key[0]) in fasttypes:
                return key[0]
            return hash(key)

        def wrap_func_with_cache(func_name, cache_field_name):
            if hasattr(self, cache_field_name):
                return

            func = getattr(self, func_name)

            def cache_wrapper(*args, **kwargs):
                cache = self.CLIENT_CACHE

                # Equivalent to not enable cache
                if cache is None:
                    return func(*args, **kwargs)

                key = make_key(args, kwargs, False)
                sentinel = object()  # unique object used to signal cache misses

                result = cache.get(key, sentinel)
                if result is not sentinel:
                    return result

                result = func(*args, **kwargs)
                cache[key] = result
                return result

            setattr(self, func_name, cache_wrapper)

        for func_name in self.func_to_cache:
            wrap_func_with_cache(func_name, f'__{func_name}.cache.dict__')

    def set_cache_dict(self, cache_dict: Optional[Dict]):
        self.CLIENT_CACHE = cache_dict

    def get_cache_dict(self):
        return self.CLIENT_CACHE

    def get_domain_list(self):
        return self.domain_list

    def set_domain_list(self, domain_list: List[str]):
        self.domain_list = domain_list

    # noinspection PyUnusedLocal
    def fallback(self, request, url, domain_index, retry_count, **kwargs):
        msg = f"请求重试全部失败: [{url}], {self.domain_list}"
        jm_log('req.fallback', msg)
        ExceptionTool.raises(msg)

    # noinspection PyMethodMayBeStatic
    def append_params_to_url(self, url, params):
        from urllib.parse import urlencode

        # 将参数字典编码为查询字符串
        query_string = urlencode(params)
        url = f"{url}?{query_string}"
        return url

    # noinspection PyMethodMayBeStatic
    def decode(self, url: str):
        if not JmModuleConfig.decode_url_when_logging or '/search/' not in url:
            return url

        from urllib.parse import unquote
        return unquote(url.replace('+', ' '))


# 基于网页实现的JmClient
class JmHtmlClient(AbstractJmClient):
    client_key = 'html'

    func_to_cache = ['search', 'fetch_detail_entity']

    def get_album_detail(self, album_id) -> JmAlbumDetail:
        return self.fetch_detail_entity(album_id, 'album')

    def get_photo_detail(self,
                         photo_id,
                         fetch_album=True,
                         fetch_scramble_id=True,
                         ) -> JmPhotoDetail:
        photo = self.fetch_detail_entity(photo_id, 'photo')

        # 一并获取该章节的所处本子
        # todo: 可优化，获取章节所在本子，其实不需要等待章节获取完毕后。
        #  可以直接调用 self.get_album_detail(photo_id)，会重定向返回本子的HTML
        if fetch_album is True:
            photo.from_album = self.get_album_detail(photo.album_id)

        return photo

    def fetch_detail_entity(self, apid, prefix):
        # 参数校验
        apid = JmcomicText.parse_to_jm_id(apid)

        # 请求
        resp = self.get_jm_html(f"/{prefix}/{apid}")

        # 用 JmcomicText 解析 html，返回实体类
        if prefix == 'album':
            return JmcomicText.analyse_jm_album_html(resp.text)

        if prefix == 'photo':
            return JmcomicText.analyse_jm_photo_html(resp.text)

    def search(self,
               search_query: str,
               page: int,
               main_tag: int,
               order_by: str,
               time: str,
               ) -> JmSearchPage:
        params = {
            'main_tag': main_tag,
            'search_query': search_query,
            'page': page,
            'o': order_by,
            't': time,
        }

        resp = self.get_jm_html(
            self.append_params_to_url('/search/photos', params),
            allow_redirects=True,
        )

        # 检查是否发生了重定向
        # 因为如果搜索的是禁漫车号，会直接跳转到本子详情页面
        if resp.redirect_count != 0 and '/album/' in resp.url:
            album = JmcomicText.analyse_jm_album_html(resp.text)
            return JmSearchPage.wrap_single_album(album)
        else:
            return JmcomicText.analyse_jm_search_html(resp.text)

    # -- 帐号管理 --

    def login(self,
              username,
              password,
              id_remember='on',
              login_remember='on',
              ):
        """
        返回response响应对象
        """

        data = {
            'username': username,
            'password': password,
            'id_remember': id_remember,
            'login_remember': login_remember,
            'submit_login': '',
        }

        resp = self.post('/login',
                         data=data,
                         allow_redirects=False,
                         )

        if resp.status_code != 301:
            ExceptionTool.raises_resp(f'登录失败，状态码为{resp.status_code}', resp)

        orig_cookies = self.get_meta_data('cookies') or {}
        new_cookies = dict(resp.cookies)
        # 重复登录下存在bug，AVS会丢失
        if 'AVS' in orig_cookies and 'AVS' not in new_cookies:
            return resp

        self['cookies'] = new_cookies

        return resp

    def favorite_folder(self,
                        page=1,
                        order_by=JmMagicConstants.ORDER_BY_LATEST,
                        folder_id='0',
                        username='',
                        ) -> JmFavoritePage:
        if username == '':
            username = self.get_username_or_raise()

        resp = self.get_jm_html(
            f'/user/{username}/favorite/albums',
            params={
                'page': page,
                'o': order_by,
                'folder_id': folder_id,
            }
        )

        return JmPageTool.parse_html_to_favorite_page(resp.text)

    # noinspection PyTypeChecker
    def get_username_or_raise(self) -> str:
        cookies = self.get_meta_data('cookies', None)
        if not cookies:
            ExceptionTool.raises('未登录，无法获取到对应的用户名，需要传username参数')

        # 解析cookies，可能需要用到 phpserialize，比较麻烦，暂不实现
        ExceptionTool.raises('需要传username参数')

    def get_jm_html(self, url, require_200=True, **kwargs):
        """
        请求禁漫网页的入口
        """
        resp = self.get(url, **kwargs)

        if require_200 is True and resp.status_code != 200:
            # 检查是否是特殊的状态码（JmModuleConfig.JM_ERROR_STATUS_CODE）
            # 如果是，直接抛出异常
            self.check_special_http_code(resp)
            # 运行到这里说明上一步没有抛异常，说明是未知状态码，抛异常兜底处理
            self.raise_request_error(resp)

        # 检查请求是否成功
        self.require_resp_success_else_raise(resp, url)

        return resp

    @classmethod
    def raise_request_error(cls, resp, msg: Optional[str] = None):
        """
        请求如果失败，统一由该方法抛出异常
        """
        if msg is None:
            msg = f"请求失败，" \
                  f"响应状态码为{resp.status_code}，" \
                  f"URL=[{resp.url}]，" \
                  + (f"响应文本=[{resp.text}]" if len(resp.text) < 200 else
                     f'响应文本过长(len={len(resp.text)})，不打印'
                     )

        ExceptionTool.raises_resp(msg, resp)

    def album_comment(self,
                      video_id,
                      comment,
                      originator='',
                      status='true',
                      comment_id=None,
                      **kwargs,
                      ) -> JmAcResp:
        data = {
            'video_id': video_id,
            'comment': comment,
            'originator': originator,
            'status': status,
        }

        # 处理回复评论
        if comment_id is not None:
            data.pop('status')
            data['comment_id'] = comment_id
            data['is_reply'] = 1
            data['forum_subject'] = 1

        jm_log('album.comment',
                 f'{video_id}: [{comment}]' +
               (f' to ({comment_id})' if comment_id is not None else '')
               )

        resp = self.post('/ajax/album_comment',
                         headers=JmModuleConfig.album_comment_headers,
                         data=data,
                         )

        ret = JmAcResp(resp)
        jm_log('album.comment', f'{video_id}: [{comment}] ← ({ret.model().cid})')

        return ret

    @classmethod
    def require_resp_success_else_raise(cls, resp, orig_req_url: str):
        """
        :param resp: 响应对象
        :param orig_req_url: /photo/12412312
        """
        resp_url: str = resp.url

        # 1. 是否是特殊的内容
        cls.check_special_text(resp)

        # 2. 检查响应发送重定向，重定向url是否表示错误网页，即 /error/xxx
        if resp.redirect_count == 0 or '/error/' not in resp_url:
            return

        # 3. 检查错误类型
        def match_case(error_path):
            return resp_url.endswith(error_path) and not orig_req_url.endswith(error_path)

        # 3.1 album_missing
        if match_case('/error/album_missing'):
            ExceptionTool.raise_missing(resp, orig_req_url)

        # 3.2 user_missing
        if match_case('/error/user_missing'):
            ExceptionTool.raises_resp('此用戶名稱不存在，或者你没有登录，請再次確認使用名稱', resp)

        # 3.3 invalid_module
        if match_case('/error/invalid_module'):
            ExceptionTool.raises_resp('發生了無法預期的錯誤。若問題持續發生，請聯繫客服支援', resp)

    @classmethod
    def check_special_text(cls, resp):
        html = resp.text
        url = resp.url

        if len(html) > 500:
            return

        for content, reason in JmModuleConfig.JM_ERROR_RESPONSE_TEXT.items():
            if content not in html:
                continue

            cls.raise_request_error(
                resp,
                f'{reason}'
                + (f': {url}' if url is not None else '')
            )

    @classmethod
    def check_special_http_code(cls, resp):
        code = resp.status_code
        url = resp.url

        error_msg = JmModuleConfig.JM_ERROR_STATUS_CODE.get(int(code), None)
        if error_msg is None:
            return

        cls.raise_request_error(
            resp,
            f"请求失败，"
            f"响应状态码为{code}，"
            f'原因为: [{error_msg}], '
            + (f'URL=[{url}]' if url is not None else '')
        )


# 基于禁漫移动端（APP）实现的JmClient
class JmApiClient(AbstractJmClient):
    client_key = 'api'
    func_to_cache = ['search', 'fetch_detail_entity']

    API_SEARCH = '/search'
    API_ALBUM = '/album'
    API_CHAPTER = '/chapter'
    API_SCRAMBLE = '/chapter_view_template'
    API_FAVORITE = '/favorite'

    def search(self,
               search_query: str,
               page: int,
               main_tag: int,
               order_by: str,
               time: str,
               ) -> JmSearchPage:
        params = {
            'main_tag': main_tag,
            'search_query': search_query,
            'page': page,
            'o': order_by,
            't': time,
        }

        resp = self.req_api(self.append_params_to_url(self.API_SEARCH, params))

        # 直接搜索禁漫车号，发生重定向的响应数据 resp.model_data
        # {
        #   "search_query": "310311",
        #   "total": 1,
        #   "redirect_aid": "310311",
        #   "content": []
        # }
        data = resp.model_data
        if data.get('redirect_aid', None) is not None:
            aid = data.redirect_aid
            return JmSearchPage.wrap_single_album(self.get_album_detail(aid))

        return JmPageTool.parse_api_to_search_page(data)

    def get_album_detail(self, album_id) -> JmAlbumDetail:
        return self.fetch_detail_entity(album_id,
                                        JmModuleConfig.album_class(),
                                        )

    def get_photo_detail(self,
                         photo_id,
                         fetch_album=True,
                         fetch_scramble_id=True,
                         ) -> JmPhotoDetail:
        photo: JmPhotoDetail = self.fetch_detail_entity(photo_id,
                                                        JmModuleConfig.photo_class(),
                                                        )
        if fetch_album or fetch_scramble_id:
            self.fetch_photo_additional_field(photo, fetch_album, fetch_scramble_id)

        return photo

    def get_scramble_id(self, photo_id, album_id=None):
        """
        带有缓存的fetch_scramble_id，缓存位于 JmModuleConfig.SCRAMBLE_CACHE
        """
        cache = JmModuleConfig.SCRAMBLE_CACHE
        if photo_id in cache:
            return cache[photo_id]

        if album_id is not None and album_id in cache:
            return cache[album_id]

        scramble_id = self.fetch_scramble_id(photo_id)
        cache[photo_id] = scramble_id
        if album_id is not None:
            cache[album_id] = scramble_id

        return scramble_id

    def fetch_detail_entity(self, apid, clazz):
        """
        请求实体类
        """
        apid = JmcomicText.parse_to_jm_id(apid)
        url = self.API_ALBUM if issubclass(clazz, JmAlbumDetail) else self.API_CHAPTER
        resp = self.req_api(
            url,
            params={
                'id': apid,
            }
        )

        self.require_resp_success(resp, url)

        return JmApiAdaptTool.parse_entity(resp.res_data, clazz)

    def fetch_scramble_id(self, photo_id):
        """
        请求scramble_id
        """
        photo_id: str = JmcomicText.parse_to_jm_id(photo_id)
        resp = self.req_api(
            self.API_SCRAMBLE,
            params={
                "id": photo_id,
                "mode": "vertical",
                "page": "0",
                "app_img_shunt": "1",
            }
        )

        scramble_id = PatternTool.match_or_default(resp.text,
                                                   JmcomicText.pattern_html_album_scramble_id,
                                                   None,
                                                   )
        if scramble_id is None:
            jm_log('api.scramble', f'未匹配到scramble_id，响应文本：{resp.text}')
            scramble_id = str(JmMagicConstants.SCRAMBLE_220980)

        return scramble_id

    def fetch_photo_additional_field(self, photo: JmPhotoDetail, fetch_album: bool, fetch_scramble_id: bool):
        """
        获取章节的额外信息
        1. scramble_id
        2. album
        如果都需要获取，会排队，效率低

        todo: 改进实现
        1. 直接开两个线程跑
        2. 开两个线程，但是开之前检查重复性
        3. 线程池，也要检查重复性
        23做法要改不止一处地方
        """
        if fetch_album:
            photo.from_album = self.get_album_detail(photo.album_id)

        if fetch_scramble_id:
            # 同album的scramble_id相同
            photo.scramble_id = self.get_scramble_id(photo.photo_id, photo.album_id)

    def setting(self) -> JmApiResp:
        """
        禁漫app的setting请求，返回如下内容（resp.res_data）
        {
          "logo_path": "https://cdn-msp.jmapiproxy1.monster/media/logo/new_logo.png",
          "main_web_host": "18-comic.work",
          "img_host": "https://cdn-msp.jmapiproxy1.monster",
          "base_url": "https://www.jmapinode.biz",
          "is_cn": 0,
          "cn_base_url": "https://www.jmapinode.biz",
          "version": "1.6.0",
          "test_version": "1.6.1",
          "store_link": "https://play.google.com/store/apps/details?id=com.jiaohua_browser",
          "ios_version": "1.6.0",
          "ios_test_version": "1.6.1",
          "ios_store_link": "https://18comic.vip/stray/",
          "ad_cache_version": 1698140798,
          "bundle_url": "https://18-comic.work/static/apk/patches1.6.0.zip",
          "is_hot_update": true,
          "api_banner_path": "https://cdn-msp.jmapiproxy1.monster/media/logo/channel_log.png?v=",
          "version_info": "\nAPP & IOS更新\nV1.6.0\n#禁漫 APK 更新拉!!\n更新調整以下項目\n1. 系統優化\n\nV1.5.9\n1. 跳錯誤新增 重試 網頁 按鈕\n2. 圖片讀取優化\n3.
          線路調整優化\n\n無法順利更新或是系統題是有風險請使用下方\n下載點2\n有問題可以到DC群反饋\nhttps://discord.gg/V74p7HM\n",
          "app_shunts": [
            {
              "title": "圖源1",
              "key": 1
            },
            {
              "title": "圖源2",
              "key": 2
            },
            {
              "title": "圖源3",
              "key": 3
            },
            {
              "title": "圖源4",
              "key": 4
            }
          ],
          "download_url": "https://18-comic.work/static/apk/1.6.0.apk",
          "app_landing_page": "https://jm365.work/pXYbfA",
          "float_ad": true
        }
        """
        resp = self.req_api('/setting')
        return resp

    def login(self,
              username,
              password,
              refresh_client_cookies=True,
              id_remember='on',
              login_remember='on',
              ) -> JmApiResp:
        """
        {
          "uid": "123",
          "username": "x",
          "email": "x",
          "emailverified": "yes",
          "photo": "x",
          "fname": "",
          "gender": "x",
          "message": "Welcome x!",
          "coin": 123,
          "album_favorites": 123,
          "s": "x",
          "level_name": "x",
          "level": 1,
          "nextLevelExp": 123,
          "exp": "123",
          "expPercent": 123,
          "badges": [],
          "album_favorites_max": 123
        }

        """
        resp = self.req_api('/login', False, data={
            'username': username,
            'password': password,
        })

        resp.require_success()
        cookies = dict(resp.resp.cookies)
        cookies.update({'AVS': resp.res_data['s']})
        self['cookies'] = cookies

        return resp

    def favorite_folder(self,
                        page=1,
                        order_by=JmMagicConstants.ORDER_BY_LATEST,
                        folder_id='0',
                        username='',
                        ) -> JmFavoritePage:
        resp = self.req_api(
            self.API_FAVORITE,
            params={
                'page': page,
                'folder_id': folder_id,
                'o': order_by,
            }
        )

        return JmPageTool.parse_api_to_favorite_page(resp.model_data)

    def req_api(self, url, get=True, **kwargs) -> JmApiResp:
        # set headers
        headers, key_ts = self.headers_key_ts
        kwargs['headers'] = headers

        if get:
            resp = self.get(url, **kwargs)
        else:
            resp = self.post(url, **kwargs)

        return JmApiResp.wrap(resp, key_ts)

    @property
    def headers_key_ts(self):
        key_ts = time_stamp()
        return JmModuleConfig.new_api_headers(key_ts), key_ts

    @classmethod
    def require_resp_success(cls, resp: JmApiResp, orig_req_url: str):
        resp.require_success()

        # 1. 检查是否 album_missing
        # json: {'code': 200, 'data': []}
        data = resp.model().data
        if isinstance(data, list) and len(data) == 0:
            ExceptionTool.raise_missing(resp, orig_req_url)

        # 2. 是否是特殊的内容
        # 暂无

    def after_init(self):
        # cookies = self.__class__.fetch_init_cookies(self)
        # self.get_root_postman().get_meta_data()['cookies'] = cookies

        self.get_root_postman().get_meta_data()['cookies'] = JmModuleConfig.get_cookies(self)
        pass


class FutureClientProxy(JmcomicClient):
    """
    在Client上做了一层线程池封装来实现异步，对外仍然暴露JmcomicClient的接口，可以看作Client的代理。
    除了使用线程池做异步，还通过加锁和缓存结果，实现同一个请求不会被多个线程发出，减少开销

    可通过插件 ClientProxyPlugin 启用本类，配置如下:
    ```yml
    plugins:
      after_init:
        - plugin: client_proxy
          kwargs:
            proxy_client_key: cl_proxy_future
    ```
    """
    client_key = 'cl_proxy_future'
    proxy_methods = ['album_comment', 'enable_cache', 'get_domain_list',
                     'get_html_domain', 'get_html_domain_all', 'get_jm_image',
                     'set_cache_dict', 'get_cache_dict', 'set_domain_list', ]

    class FutureWrapper:
        def __init__(self, future):
            from concurrent.futures import Future
            future: Future
            self.future = future
            self.done = False
            self._result = None

        def result(self):
            if not self.done:
                result = self.future.result()
                self._result = result
                self.done = True
                self.future = None  # help gc

            return self._result

    def __init__(self,
                 client: JmcomicClient,
                 max_workers=None,
                 executors=None,
                 ):
        self.client = client
        for method in self.proxy_methods:
            setattr(self, method, getattr(client, method))

        if executors is None:
            from concurrent.futures import ThreadPoolExecutor
            executors = ThreadPoolExecutor(max_workers)

        self.executors = executors
        self.future_dict: Dict[str, FutureClientProxy.FutureWrapper] = {}
        from threading import Lock
        self.lock = Lock()

    def get_album_detail(self, album_id) -> JmAlbumDetail:
        album_id = JmcomicText.parse_to_jm_id(album_id)
        cache_key = f'album_{album_id}'
        future = self.get_future(cache_key, task=lambda: self.client.get_album_detail(album_id))
        return future.result()

    def get_future(self, cache_key, task):
        if cache_key in self.future_dict:
            return self.future_dict[cache_key]

        with self.lock:
            if cache_key in self.future_dict:
                return self.future_dict[cache_key]

            future = self.FutureWrapper(self.executors.submit(task))
            self.future_dict[cache_key] = future
            return future

    def get_photo_detail(self, photo_id, fetch_album=True, fetch_scramble_id=True) -> JmPhotoDetail:
        photo_id = JmcomicText.parse_to_jm_id(photo_id)
        client: JmcomicClient = self.client
        futures = [None, None, None]
        results = [None, None, None]

        # photo_detail
        photo_future = self.get_future(f'photo_{photo_id}',
                                       lambda: client.get_photo_detail(photo_id,
                                                                       False,
                                                                       False)
                                       )
        futures[0] = photo_future

        # fetch_album
        if fetch_album:
            album_future = self.get_future(f'album_{photo_id}',
                                           lambda: client.get_album_detail(photo_id))
            futures[1] = album_future
        else:
            results[1] = None

        # fetch_scramble_id
        if fetch_scramble_id and isinstance(client, JmApiClient):
            client: JmApiClient
            scramble_future = self.get_future(f'scramble_id_{photo_id}',
                                              lambda: client.get_scramble_id(photo_id))
            futures[2] = scramble_future
        else:
            results[2] = ''

        # wait finish
        for i, f in enumerate(futures):
            if f is None:
                continue
            results[i] = f.result()

        # compose
        photo: JmPhotoDetail = results[0]
        album = results[1]
        scramble_id = results[2]

        if album is not None:
            photo.from_album = album
        if scramble_id != '':
            photo.scramble_id = scramble_id

        return photo

    def search(self, search_query: str, page: int, main_tag: int, order_by: str, time: str) -> JmSearchPage:
        cache_key = f'search_query_{search_query}_page_{page}_main_tag_{main_tag}_order_by_{order_by}_time_{time}'
        future = self.get_future(cache_key, task=lambda: self.client.search(search_query, page, main_tag, order_by, time))
        return future.result()
