import requests
import requests_html
from io import BytesIO
from PIL import Image
from icalendar import Calendar, Event, Alarm
from datetime import datetime, timedelta
import random
import math
import re
import os
import getpass
from enum import Enum, unique
import sys

DBG_MODE = False

@unique
class ErrorCode(Enum):
    CAPTCHA_FETCH_ERROR = -1, '验证码获取失败'
    LOGIN_ERROR = -2, '登录失败'
    XHRSession_Error = -3, 'XHRSession ID获取失败'
    YEAR_FETCH_ERROR = -4, '教学年获取失败'
    TERM_FETCH_ERROR = -5, '学期获取失败'
    COURSE_FETCH_ERROR = -6, '课程表获取失败'
    CONNECTION_ERROR = -7, '网络连接失败'

    def __init__(self, errorCode, errorMsg):
        self.errorcode = errorCode
        self.errorMsg = errorMsg


class MyException(Exception):
    def __init__(self, errorCode: ErrorCode, detail: str):
        super().__init__(errorCode, detail)
        self.detail = detail
        self.errorCode = errorCode

    def __str__(self):
        return '错误代码:%d   错误描述:%s   详细信息:%s' % (self.errorCode.errorcode, self.errorCode.errorMsg, self.detail)


class CourseInfo:
    def __init__(self, teacherId, teacherName, courseId, courseName, roomId, roomName, vaildWeeks):
        self.teacherId = teacherId
        self.teacherName = teacherName
        self.courseId = courseId
        self.courseName = courseName
        self.roomId = roomId
        self.roomName = roomName
        self.vaildWeeks = vaildWeeks  # 01组成的字符串，代表了一年的53周
        self.day = None
        self.courses = []


timetable = [('08:15', '09:00'),
             ('09:00', '09:45'),
             ('10:05', '10:50'),
             ('10:50', '11:35'),
             ('13:00', '13:45'),
             ('13:45', '14:30'),
             ('14:50', '15:35'),
             ('15:35', '16:20'),
             ('18:00', '18:45'),
             ('18:45', '19:30'),
             ('19:30', '20:15'),
             ('20:15', '21:00'),
             ('21:00', '21:45'),
             ('21:45', '22:30')]


class SuesApi:

    def __init__(self):
        self.session = None
        self.xhrOriSessionId = None

        # 用到的正则表达式
        self.activityMatchRe = re.compile(r'.*new.*TaskActivity\(.*\).*;$')
        self.activityExtractRe = re.compile(r'".*?"')
        self.indexMatchRe = re.compile(r'.*index.*=\d\*.*\+\d.*;$')
        self.marshallMatchRe = re.compile(r'.*marshalTable\(.*?\).*;$')
        self.timeExtractRe = re.compile(r'\(.*\)')
        self.squareBracketExprRe = re.compile(r'\[.*\]')
        self.yearMatchRe = re.compile(r'.*CourseTable\(.*?\).*;$')

    def newSession(self):
        """
        创建新会话，本方法必须在所有函数之前调用
        """
        # proxies = {'http': 'socks5://127.0.0.1:1085',
        #            'https': 'socks5://127.0.0.1:1085'}
        reqHeader = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/62.0.3202.9 Safari/537.36'
        }
        self.session = requests_html.HTMLSession()
        self.session.headers = reqHeader
        # self.session.proxies = proxies

        # 测试连接
        try:
            self.session.get('http://jxxt.sues.edu.cn/', timeout=10)
        except requests.exceptions.RequestException as e:  # This is the correct syntax
            raise MyException(ErrorCode.CONNECTION_ERROR, '访问教学管理系统主页出错,请检查连接\n' + str(e))

            self.xhrOriSessionId = self._getXHROriSessionID()
            self.xhrSessionId = self._getXHRCallSessionId()

        def getCaptha(self):
            """
            获取验证码
            :return: 验证码图像(bytearray)）
            """
            if not self.session:
                raise MyException(ErrorCode.CAPTCHA_FETCH_ERROR, 'session对象没有被建立，是否忘记调用了 SuesApi.newSession?')

            try:
                r = self.session.get('http://jxxt.sues.edu.cn/eams/captcha/image.action')
            except requests.exceptions.RequestException as e:  # This is the correct syntax
                raise MyException(ErrorCode.CAPTCHA_FETCH_ERROR, str(e))

            if r.status_code == 200:
                return r.content
            else:
                raise MyException(ErrorCode.CAPTCHA_FETCH_ERROR, '验证码获取失败，返回值异常' + str(r.status_code))

        def login(self, username: str, passwd: str, captcha: str):
            """
            登录
            :param username: 学号
            :param passwd: 教学管理系统密码
            :param captcha: 验证码
            """
            if not self.session:
                raise MyException(ErrorCode.LOGIN_ERROR, 'session对象没有被建立，是否忘记调用了 SuesApi.newSession?')

            data = {'loginForm.name': username,
                    'loginForm.password': passwd,
                    'encodedPassword': '',
                    'loginForm.captcha': captcha}
            try:
                r = self.session.post('http://jxxt.sues.edu.cn/eams/login.action', data)
            except requests.exceptions.RequestException as e:  # This is the correct syntax
                raise MyException(ErrorCode.LOGIN_ERROR, str(e))

            errorMsg = r.html.find('ul.errorMessage>li>span', first=True)
            if errorMsg:
                raise MyException(ErrorCode.LOGIN_ERROR, errorMsg.text)

        def _getXHROriSessionID(self):
            """
            获取生成XHRSessionID需要的XHROriSessionID
            :return: XHROriSessionID
            """
            if not self.session:
                raise MyException(ErrorCode.XHRSession_Error, 'session对象没有被建立，是否忘记调用了 SuesApi.newSession?')

            # 获取engine.js
            try:
                r = self.session.get('http://jxxt.sues.edu.cn/eams/dwr/engine.js')
            except requests.exceptions.RequestException as e:  # This is the correct syntax
                raise MyException(ErrorCode.XHRSession_Error, str(e))

            sessionStrBeg = r.text.find('dwr.engine._origScriptSessionId')
            sessionStrEnd = r.text.find('\n', sessionStrBeg)
            sessionStr = r.text[sessionStrBeg:sessionStrEnd]
            sessionStrBeg = sessionStr.find('"')
            sessionStrEnd = sessionStr.rfind('"')
            sessionStr = sessionStr[sessionStrBeg + 1:sessionStrEnd]
            return sessionStr

        def _getXHRCallSessionId(self):
            """
            获取XHRSessionID，XHRSessionID=XHROriSessionID+3位随机数
            :return: XHRSessionID
            """
            if not self.xhrOriSessionId:
                raise MyException(ErrorCode.XHRSession_Error,
                                  'xhrOriSessionId对象没有被建立，是否忘记调用了 SuesApi._getXHROriSessionID?')
            return self.xhrOriSessionId + str(math.floor(random.random() * 1000))

        def getYears(self):
            """
            获取教学系统允许查询的教学年
            :return: 字符串列表 例：['2019-2020']
            """
            if not self.xhrSessionId or not self.session:
                raise MyException(ErrorCode.YEAR_FETCH_ERROR, 'session或xhrSessionId对象没有被建立，是否忘记调用了 SuesApi.newSession?')

            # XHR调用请求参数
            payload = {
                'callCount': '1',
                'page': '/eams/courseTableForStd.action?method=stdHome',
                'httpSessionId': '',
                'scriptSessionId': self.xhrSessionId,
                'c0-scriptName': 'semesterDao',
                'c0-methodName': 'getYearsOrderByDistance',
                'c0-id': '0',
                'c0-param0': 'string:1',
                'batchId': '0'
            }
            try:
                r = self.session.post(
                    'http://jxxt.sues.edu.cn/eams/dwr/call/plaincall/semesterDao.getYearsOrderByDistance.dwr',
                    data=payload)
            except requests.exceptions.RequestException as e:  # This is the correct syntax
                raise MyException(ErrorCode.YEAR_FETCH_ERROR, str(e))

            yearList = self.squareBracketExprRe.findall(r.text)[0][1:-1].replace('"', '').split(',')

            return yearList

        def getTerms(self, yearStr: str):
            """
            获取当前教学年对应的学期选项
            :param yearStr: self.getYears获取的年份字符串 例:'2019-2020'
            :return: 学期列表 例：['1','2']
            """
            if not self.xhrSessionId or not self.session:
                raise MyException(ErrorCode.TERM_FETCH_ERROR, 'session或xhrSessionId对象没有被建立，是否忘记调用了 SuesApi.newSession?')

            # XHR调用请求参数
            payload = {
                'callCount': '1',
                'page': '/eams/courseTableForStd.action?method=stdHome',
                'httpSessionId': '',
                'scriptSessionId': self.xhrSessionId,
                'c0-scriptName': 'semesterDao',
                'c0-methodName': 'getTermsOrderByDistance',
                'c0-id': '0',
                'c0-param0': 'string:1',
                'c0-param1': 'string:' + yearStr,
                'batchId': '1'
            }
            try:
                r = self.session.post(
                    'http://jxxt.sues.edu.cn/eams/dwr/call/plaincall/semesterDao.getTermsOrderByDistance.dwr',
                    data=payload)
            except requests.exceptions.RequestException as e:  # This is the correct syntax
                raise MyException(ErrorCode.TERM_FETCH_ERROR, str(e))

            semesterList = self.squareBracketExprRe.findall(r.text)[0][1:-1].replace('"', '').split(',')
            return semesterList

        def getCourseTable(self, yearStr: str, semester: str):
            """
            获取课程列表
            :param yearStr: self.getYears获取的年份字符串 例:'2019-2020'
            :param semester: 学期 例:'1'
            :return: 课表年份, 起始周(后来发现没有用), CourseInfo列表 表中每一项代表教学管理系统的一个格子，相应需要创建一个日程
            """
            if not self.session:
                raise MyException(ErrorCode.COURSE_FETCH_ERROR, 'session对象没有被建立，是否忘记调用了 SuesApi.newSession?')

            # get SemesterID and other stuff
            r = self.session.get('http://jxxt.sues.edu.cn/eams/courseTableForStd.action?method=stdHome')

            semesterId = r.html.find('input[name=semester\\.id]', first=True).attrs['value']
            # what if the webpage changed?
            courseRequestUrl = 'http://jxxt.sues.edu.cn/eams/' + \
                               r.html.find('td.frameTable_content>iframe', first=True).attrs[
                                   'src']
            payload = {
                'ignoreHead': '1',
                'semester.id': 'semesterId',
                'semester.calendar.id': '1',
                'semester.schoolYear': yearStr,
                'semester.name': semester,
                'startWeek': '1'
            }
            try:
                r = self.session.post(courseRequestUrl, data=payload)
            except requests.exceptions.RequestException as e:  # This is the correct syntax
                raise MyException(ErrorCode.COURSE_FETCH_ERROR, str(e))

            # 寻找特定的一个js脚本
            script = r.html.find('script', containing='new TaskActivity')
            assert (len(script) == 1)
            script = script[0]
            scriptStr = script.html.replace('&#13;', '\r\n')

            rltYear = None  # 返回的起始年份
            rltCouseList = []  # 返回的课程列表，
            rltStartWeek = None  # 返回的起始周

            # 逐行解析js脚本，获取其中的课程信息
            for line in scriptStr.splitlines():
                if (self.activityMatchRe.match(line)):
                    # 新课程
                    curCourse = CourseInfo(*(i[1:-1] for i in self.activityExtractRe.findall(line)))
                    rltCouseList.append(curCourse)
                elif (self.indexMatchRe.match(line)):
                    # 当前课程的节次信息
                    line = line.replace(' ', '')
                    beg = line.find('=') + 1
                    line = line[beg:-1]

                    day, course = line.replace('index =', '').split('*unitCount+')
                    rltCouseList[-1].day = day
                    rltCouseList[-1].courses.append(course)
                elif (self.marshallMatchRe.match(line)):
                    # 起始周信息
                    rltStartWeek, _, _ = self.timeExtractRe.findall(line)[0][1:-1].split(',')
                elif (self.yearMatchRe.match(line)):
                    # 当前年份信息
                    rltYear, _ = self.timeExtractRe.findall(line)[0][1:-1].split(',')
            return rltYear, rltStartWeek, rltCouseList


def cvt2Caldav(startYear: str, startWeek: int, courseList: list, alarmTime: int, icsFileName: str):
    """
    将课程信息
    :param startYear: 课表年份，可以通过SuesApi.getCourseTable获得
    :param startWeek: 课表起始周，可以通过SuesApi.getCourseTable获得
    :param courseList: 课程信息列表，可以通过SuesApi.getCourseTable获得
    :param alarmTime: 提前提醒分钟数，正整数
    :param icsFileName: ics文件的名称
    """
    cal = Calendar()
    weekExtractRe = re.compile(r'[1]+')

    for curCourse in courseList:
        # 遍历开课时间段，每个开课时间段（周次）对应课程表上的一个格子，创建一个日程
        for validweek in weekExtractRe.finditer(curCourse.vaildWeeks):
            begWeek = validweek.start() + 1  # 当前开课周次起始周
            endWeek = validweek.end()  # 当前开课周次结束日期

            courseTimes = sorted(curCourse.courses)
            begTime = timetable[int(courseTimes[0])][0]  # 上课时间
            endTime = timetable[int(courseTimes[-1])][-1]  # 下课时间

            startDayFrom = datetime.strptime(
                ''.join([str(startYear), '-W', str(begWeek), '-', str((int(curCourse.day) + 1) % 7), ' ',
                         begTime]), "%Y-W%W-%w %H:%M")  # 第一次上课时间
            startDayTo = datetime.strptime(
                ''.join([str(startYear), '-W', str(begWeek), '-', str((int(curCourse.day) + 1) % 7), ' ',
                         endTime]), "%Y-W%W-%w %H:%M")  # 第一次下课时间
            untilDay = datetime.strptime(
                ''.join([str(startYear), '-W', str(endWeek), '-', str((int(curCourse.day) + 1) % 7), ' ',
                         endTime]), "%Y-W%W-%w %H:%M")  # 最后一次下课时间

            # 调试信息输出
            print('正在添加日程： %23s\t%s\t%d-%d周\t%d-%d节\t' % (
                curCourse.courseName,
                curCourse.teacherName,
                begWeek - int(startWeek) + 1,
                endWeek - int(startWeek) + 1,
                int(courseTimes[0]) + 1,
                int(courseTimes[-1]) + 1))

            event = Event()
            event.add('summary', curCourse.courseName + ' ' + curCourse.teacherName)
            event.add('dtstart', startDayFrom)
            event.add('dtend', startDayTo)
            event.add('location', curCourse.roomName)
            event.add('rrule', {'freq': 'weekly', 'until': untilDay})  # 每周重复，直到停止

            eventAlarm = Alarm()
            eventAlarm.add('action', 'display')
            eventAlarm.add('description', curCourse.courseName + ' ' + curCourse.roomName)
            eventAlarm.add('trigger', timedelta(minutes=-abs(alarmTime)))

            event.add_component(eventAlarm)
            cal.add_component(event)
    # 写出ics文件
    with open(os.path.join(icsFileName), 'wb') as f:
        f.write(cal.to_ical())
        f.close()


if __name__ == '__main__':
    print('''
 ____  _   _ _____ ____      ____ ____   ____   _____           _ 
/ ___|| | | | ____/ ___|    / ___|___ \ / ___| |_   _|__   ___ | |
\___ \| | | |  _| \___ \    \___ \ __) | |       | |/ _ \ / _ \| |
 ___) | |_| | |___ ___) |    ___) / __/| |___    | | (_) | (_) | |
|____/ \___/|_____|____/    |____/_____|\____|   |_|\___/ \___/|_|   Ver 1.0

SUES 课表转iCalendar日程工具
源代码/Issue/贡献 https://github.com/GammaPi/SuesS2C
by XtTech 
    ''')

    # 1.get captha
    try:
        suesApi = SuesApi()
        print('连接教学管理系统中... 请确认http://jxxt.sues.edu.cn能访问')
        suesApi.newSession()
        print('获取验证码中,验证码将在另外窗口中弹出...')
        capthaBytes = suesApi.getCaptha()
        i = Image.open(BytesIO(capthaBytes))
        i.show()

        # todo:verify user input
        username = input('用户名:')
        passwd = getpass.getpass("密码(输入时不显示):");
        captcha = input('验证码:')
        print(captcha)

        suesApi.login(username, passwd, captcha)

        yearList = suesApi.getYears()
        for i, year in enumerate(yearList):
            print(i, ':', year)
        yearSelection = yearList[int(input('Please select a year:'))]

        termList = suesApi.getTerms(yearSelection)
        sorted(termList)
        for i, term in enumerate(termList):
            print(i, ':第%s学期' % term)
        termSelection = termList[int(input('Please select a Term:'))]

        # todo: check input
        rltYear, startWeek, courseList = suesApi.getCourseTable(yearSelection, termSelection)

        cvt2Caldav(rltYear, startWeek, courseList)
    except MyException as e:
        print('[异常]', e, file=sys.stderr)
        if DBG_MODE:
            raise e
    except KeyboardInterrupt as e:
        print('KeyboardInterrupt')
    except BaseException as e:
        print('[异常] 遇到未识别的异常，可能因为BUG或教学系统API变化导致，非常抱歉，请更新软件或联系开发者！\n 错误信息：' + str(e), file=sys.stderr)
        if DBG_MODE:
            raise e
