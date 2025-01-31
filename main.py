import asyncio
from api.qinglong import QlApi, QlOpenApi
from api.send import SendApi
from config import (
    jd_login_url,
    auto_move,
    qinglong_data,
    user_datas,
    auto_shape_recognition,
)
import json
from loguru import logger
import os
from playwright.async_api import Playwright, async_playwright
import random
import traceback
from typing import Union
from utils.consts import (
    supported_types,
    supported_colors
)
from utils.tools import (
    get_tmp_dir,
    get_img_bytes,
    get_forbidden_users_dict,
    filter_forbidden_users,
    save_img,
    get_ocr,
    get_word,
    get_shape_location_by_type,
    get_shape_location_by_color,
    rgba2rgb,
    send_msg,
    solve_slider_captcha,
    ddddocr_find_bytes_pic
)

"""
基于playwright做的
"""
logger.add(
    sink="main.log",
    level="DEBUG"
)


async def auto_move_slide(page, retry_times: int = 2):
    """
    自动识别移动滑块验证码
    """
    from config import slide_difference
    for i in range(retry_times):
        logger.info(f'第{i + 1}次尝试自动移动滑块中...')
        try:
            # 查找小图
            await page.wait_for_selector('#small_img', state='visible', timeout=3000)
        except Exception as e:
            # 未找到元素，认为成功，退出循环
            logger.info('未找到小图,退出移动滑块')
            break

        # 获取 src 属性
        small_src = await page.locator('#small_img').get_attribute('src')
        background_src = await page.locator('#cpc_img').get_attribute('src')

        # 获取 bytes
        small_img_bytes = get_img_bytes(small_src)
        background_img_bytes = get_img_bytes(background_src)

        # 获取滑块
        slider = await page.wait_for_selector('img.move-img')
        await asyncio.sleep(1)

        # 获取要移动的长度
        distance = ddddocr_find_bytes_pic(small_img_bytes, background_img_bytes)
        await asyncio.sleep(1)
        # 移动滑块
        await solve_slider_captcha(page, slider, distance, slide_difference)
        await asyncio.sleep(1)


async def auto_shape(page, retry_times: int = 5):
    ocr = get_ocr(beta=True)
    """
    自动识别滑块验证码
    """
    for i in range(retry_times):
        logger.info(f'第{i + 1}次自动识别形状中...')
        try:
            # 查找小图
            await page.wait_for_selector('div.captcha_footer img', state='visible', timeout=3000)
        except Exception as e:
            # 未找到元素，认为成功，退出循环
            logger.info('未找到形状图,退出识别状态')
            break

        tmp_dir = get_tmp_dir()

        background_img_path = os.path.join(tmp_dir, f'background_img.png')
        # 获取大图元素
        background_locator = page.locator('#cpc_img')
        # 获取元素的位置和尺寸
        backend_bounding_box = await background_locator.bounding_box()
        backend_top_left_x = backend_bounding_box['x']
        backend_top_left_y = backend_bounding_box['y']

        # 截取元素区域
        await page.screenshot(path=background_img_path, clip=backend_bounding_box)

        # 获取 图片的src 属性和button按键
        word_img_src = await page.locator('div.captcha_footer img').get_attribute('src')
        button = page.locator('div.captcha_footer button.sure_btn')

        # 找到刷新按钮
        refresh_button = page.locator('div.captcha_header img.jcap_refresh')


        # 获取文字图并保存
        word_img_bytes = get_img_bytes(word_img_src)
        rgba_word_img_path = save_img('rgba_word_img', word_img_bytes)

        # 文字图是RGBA的，有蒙板识别不了，需要转成RGB
        rgb_word_img_path = rgba2rgb('rgb_word_img', rgba_word_img_path)

        # 获取问题的文字
        word = get_word(ocr, rgb_word_img_path)

        if word.find('色') > 0:
            target_color = word.split('请选出图中')[1].split('的图形')[0]
            if target_color in supported_colors:
                logger.info(f'正在点击中......')
                # 获取点的中心点
                center_x, center_y = get_shape_location_by_color(background_img_path, target_color)
                if center_x is None and center_y is None:
                    logger.info(f'识别失败,刷新中......')
                    await refresh_button.click()
                    await asyncio.sleep(random.uniform(2, 4))
                    continue
                # 得到网页上的中心点
                x, y = backend_top_left_x + center_x, backend_top_left_y + center_y
                # 点击图片
                await page.mouse.click(x, y)
                await asyncio.sleep(random.uniform(1, 4))
                # 点击确定
                await button.click()
                await asyncio.sleep(random.uniform(1, 4))
                continue
            else:
                logger.info(f'不支持{target_color},刷新中......')
                # 刷新
                await refresh_button.click()
                await asyncio.sleep(random.uniform(1, 4))
                continue

        else:
            shape_type = word.split('请选出图中的')[1]
            if shape_type in supported_types:
                logger.info(f'已找到图形,点击中......')
                if shape_type == "圆环":
                    shape_type = shape_type.replace('圆环', '圆形')
                # 获取点的中心点
                center_x, center_y = get_shape_location_by_type(background_img_path, shape_type)
                if center_x is None and center_y is None:
                    logger.info(f'识别失败,刷新中......')
                    await refresh_button.click()
                    await asyncio.sleep(random.uniform(1, 4))
                    continue
                # 得到网页上的中心点
                x, y = backend_top_left_x + center_x, backend_top_left_y + center_y
                # 点击图片
                await page.mouse.click(x, y)
                await asyncio.sleep(random.uniform(1, 4))
                # 点击确定
                await button.click()
                await asyncio.sleep(random.uniform(1, 4))
                continue
            else:
                logger.info(f'不支持{shape_type},刷新中......')
                # 刷新
                await refresh_button.click()
                await asyncio.sleep(random.uniform(1, 4))
                continue


async def get_jd_pt_key(playwright: Playwright, user) -> Union[str, None]:
    """
    获取jd的pt_key
    """

    try:
        from config import headless
    except ImportError:
        headless = False

    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context()

    try:
        page = await context.new_page()
        # 关闭Webdriver属性,绕过Webdriver检测
        js = """Object.defineProperties(navigator, {webdriver:{get:()=>undefined}});"""
        await page.add_init_script(js)
        await page.goto(jd_login_url)
        await page.get_by_text("账号密码登录").click()

        username_input = page.get_by_placeholder("账号名/邮箱/手机号")
        await username_input.click()
        for u in user:
            await username_input.type(u, no_wait_after=True)
            await asyncio.sleep(random.random() / 10)

        password_input = page.get_by_placeholder("请输入密码")
        await password_input.click()
        password = user_datas[user]["password"]
        for p in password:
            await password_input.type(p, no_wait_after=True)
            await asyncio.sleep(random.random() / 10)

        await page.get_by_role("checkbox").check()
        await page.get_by_text("登 录").click()

        # 自动识别移动滑块验证码
        if auto_move:
            # 关键的sleep
            await asyncio.sleep(1)
            await auto_move_slide(page, retry_times=5)

            # 自动验证形状验证码
            if auto_shape_recognition:
                await asyncio.sleep(1)
                await auto_shape(page, retry_times=30)

        # 等待验证码通过
        logger.info("等待获取cookie...")
        await page.wait_for_selector('#msShortcutMenu', state='visible', timeout=120000)

        cookies = await context.cookies()
        for cookie in cookies:
            if cookie['name'] == 'pt_key':
                pt_key = cookie["value"]
                return pt_key

        return None

    except Exception as e:
        traceback.print_exc()
        return None

    finally:
        await context.close()
        await browser.close()


async def get_ql_api(ql_data):
    """
    封装了QL的登录
    """
    logger.info("开始获取QL登录态......")

    # 优化client_id和client_secret
    client_id = ql_data.get('client_id')
    client_secret = ql_data.get('client_secret')
    if client_id and client_secret:
        logger.info("使用client_id和client_secret登录......")
        qlapi = QlOpenApi(ql_data["url"])
        response = qlapi.login(client_id=client_id, client_secret=client_secret)
        if response['code'] == 200:
            logger.info("client_id和client_secret正常可用......")
            return qlapi
        else:
            logger.info("client_id和client_secret异常......")

    qlapi = QlApi(ql_data["url"])

    # 其次用token
    token = ql_data.get('token')
    if token:
        logger.info("已设置TOKEN,开始检测TOKEN状态......")
        qlapi.login_by_token(token)

        # 如果token失效，就用账号密码登录
        response = await qlapi.get_envs()
        if response['code'] == 401:
            logger.info("Token已失效, 正使用账号密码获取QL登录态......")
            response = qlapi.login_by_username(ql_data.get("username"), ql_data.get("password"))
            if response.status_code != 200:
                logger.error(f"账号密码登录失败. response: {response}")
                raise Exception(f"账号密码登录失败. response: {response}")
        else:
            logger.info("Token正常可用......")
    else:
        # 最后用账号密码
        logger.info("正使用账号密码获取QL登录态......")
        response = qlapi.login_by_username(ql_data.get("username"), ql_data.get("password"))
        if response.status_code != 200:
            logger.error(f"账号密码登录失败. response: {response}")
            raise Exception(f"账号密码登录失败.response: {response}")
    return qlapi


async def main():
    try:
        qlapi = await get_ql_api(qinglong_data)
        send_api = SendApi("ql")
        # 拿到禁用的用户列表
        response = await qlapi.get_envs()
        if response['code'] == 200:
            logger.info("获取环境变量成功")
        else:
            logger.error(f"获取环境变量失败， response: {response}")
            raise Exception(f"获取环境变量失败， response: {response}")

        user_info = response['data']
        # 获取禁用用户
        forbidden_users = [x for x in user_info if x['name'] == 'JD_COOKIE' and x['status'] == 1]

        if not forbidden_users:
            logger.info("所有COOKIE环境变量正常，无需更新")
            return

        # 获取需要的字段
        filter_users_list = filter_forbidden_users(forbidden_users, ['id', 'value', 'remarks', 'name'])

        # 生成字典
        user_dict = get_forbidden_users_dict(filter_users_list, user_datas)

        # 登录JD获取pt_key
        async with async_playwright() as playwright:
            for user in user_dict:
                logger.info(f"开始更新{user}")
                pt_key = await get_jd_pt_key(playwright, user)
                if pt_key is None:
                    logger.error(f"获取pt_key失败")
                    await send_msg(send_api, send_type=1, msg=f"{user} 更新失败")
                    continue

                req_data = user_dict[user]
                req_data["value"] = f"pt_key={pt_key};pt_pin={user_datas[user]['pt_pin']};"
                logger.info(f"更新内容为{req_data}")
                data = json.dumps(req_data)
                response = await qlapi.set_envs(data=data)
                if response['code'] == 200:
                    logger.info(f"{user}更新成功")
                else:
                    logger.error(f"{user}更新失败, response: {response}")
                    await send_msg(send_api, send_type=1, msg=f"{user} 更新失败")
                    continue

                data = bytes(f"[{req_data['id']}]", 'utf-8')
                response = await qlapi.envs_enable(data=data)
                if response['code'] == 200:
                    logger.info(f"{user}启用成功")
                    await send_msg(send_api, send_type=0, msg=f"{user} 更新成功")
                else:
                    logger.error(f"{user}启用失败, response: {response}")

    except Exception as e:
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())
