import requests
import requests_html
from io import BytesIO
from PIL import Image
from icalendar import Calendar, Event, Alarm
from datetime import datetime, timedelta
from dateutil import tz
import random
import math
import re
import os
import getpass
from enum import Enum, unique
import sys
import copy
from functools import cmp_to_key

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
    INPUT_ERROR = -8, '用户输入错误'
    API_CHANGED = -9, '教学系统API有变化'

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
    def __init__(self, teacherId, teacherName, courseId, courseName, roomId, roomName, validweeks):
        self.teacherId = teacherId
        self.teacherName = teacherName
        self.courseId = courseId
        self.courseName = courseName
        self.roomId = roomId
        self.roomName = roomName
        self.validweeks = validweeks  # 01组成的字符串，代表了一年的53周
        self.day = None
        self.courses = []

    def canMergeValidWeek(self, otherCourseInfo):
        # 这里必须要把星期和上课时间补充完整后才能进行合并
        assert self.day != None
        assert len(self.courses) != 0

        # 合并的条件是两个课程除了validweeks其他信息都相同，且validweeks长度相同，内容不同 validweeks长度53
        return len(self.validweeks) == 53 \
               and len(self.validweeks) == len(otherCourseInfo.validweeks) \
               and self.validweeks != otherCourseInfo.validweeks \
               and self.teacherId == otherCourseInfo.teacherId \
               and self.courseId == otherCourseInfo.courseId \
               and self.roomId == otherCourseInfo.roomId \
               and self.day == otherCourseInfo.day

    def mergeValidWeek(self, otherValidWeeks: str):
        """
        对字符串进行合并，self.validweeks+=otehrValidWeeks
        :param otherValidWeeks:
        """
        assert len(self.validweeks) == len(otherValidWeeks)
        self.validweeks += otherValidWeeks


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
             ('16:30', '17:15'),
             ('17:15', '18:00')]
DEFtimeTable = timetable.copy()
DEFtimeTable[2] = ('10:25', '11:10')
DEFtimeTable[3] = ('11:10', '11:55')

# 学校课程 13-14节的时间在第九节之前需要自定义顺序，否则不能正常表示
realTimeTableOrder = [1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14, 9, 10]

def cmp_courseTime(t1: int, t2: int):
    if realTimeTableOrder[t1] < realTimeTableOrder[t2]:
        return -1
    if realTimeTableOrder[t1] > realTimeTableOrder[t2]:
        return 1
    return 0

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
            r = self.session.get('http://jxxt.sues.edu.cn/eams/captcha/image.action', timeout=10)
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
            r = self.session.post('http://jxxt.sues.edu.cn/eams/login.action', data, timeout=10)
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
            r = self.session.get('http://jxxt.sues.edu.cn/eams/dwr/engine.js', timeout=10)
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
                data=payload, timeout=10)
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
                data=payload, timeout=10)
        except requests.exceptions.RequestException as e:  # This is the correct syntax
            raise MyException(ErrorCode.TERM_FETCH_ERROR, str(e))

        semesterList = self.squareBracketExprRe.findall(r.text)[0][1:-1].replace('"', '').split(',')
        return semesterList

    def getCourseTable(self, yearStr: str, semester: str):
        """
        获取课程列表
        :param yearStr: self.getYears获取的年份字符串 例:'2019-2020'
        :param semester: 学期 例:'1'
        :return: 课表年份, 教学活动起始(相对于全年),教学活动起始周(相对于第二个参数)，教学活动结束周(相对于第二个参数), CourseInfo列表 表中每一项代表教学管理系统的一个格子，相应需要创建一个日程
        """
        if not self.session:
            raise MyException(ErrorCode.COURSE_FETCH_ERROR, 'session对象没有被建立，是否忘记调用了 SuesApi.newSession?')

        # get SemesterID and other stuff
        try:
            r = self.session.get('http://jxxt.sues.edu.cn/eams/courseTableForStd.action?method=stdHome', timeout=10)
        except requests.exceptions.RequestException as e:  # This is the correct syntax
            raise MyException(ErrorCode.COURSE_FETCH_ERROR, str(e))

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

        print('获取课程信息中...(1/3)')
        try:
            r = self.session.post(courseRequestUrl, data=payload, timeout=10)
        except requests.exceptions.RequestException as e:  # This is the correct syntax
            raise MyException(ErrorCode.COURSE_FETCH_ERROR, str(e))

        print('解析课程信息中...(2/3)')
        # 寻找特定的一个js脚本
        script = r.html.find('script', containing='new TaskActivity')
        if (len(script) != 1):
            raise MyException(ErrorCode.COURSE_FETCH_ERROR, '课表获取失败，可能是因为该时间段没有课程？请检查学期、时间的选择，如果还有问题请联系开发者。')
        script = script[0]
        scriptStr = script.html.replace('&#13;', '\r\n')

        rltStartYear = None  # 返回的起始年份
        rltCouseList = []  # 返回的课程列表，
        rltAllOccupyWeek = None  # 教学活动起始周
        rltAllStartWeek = None  # 课表相对起始周 从1开始，一般都为1
        rltAllEndWeek = None  # 返回的结束周 从1开始
        # 逐行解析js脚本，获取其中的课程信息

        unMergedCouseList = []  # 未合并的课程列表，
        unMergedCourseDict = {}
        # 提取并补充设置所有课程信息的上课星期、上课时间
        for line in scriptStr.splitlines():
            if (self.activityMatchRe.match(line)):
                # 新课程
                curCourse = CourseInfo(*(i[1:-1] for i in self.activityExtractRe.findall(line)))
                unMergedCouseList.append(curCourse)
            elif (self.indexMatchRe.match(line)):
                # 当前课程的节次信息
                line = line.replace(' ', '')
                beg = line.find('=') + 1
                line = line[beg:-1]

                day, course = line.replace('index =', '').split('*unitCount+')
                unMergedCouseList[-1].day = day
                unMergedCouseList[-1].courses.append(course)
            elif (self.marshallMatchRe.match(line)):
                # 起始周信息
                rltAllOccupyWeek, rltAllStartWeek, rltAllEndWeek = self.timeExtractRe.findall(line)[0][1:-1].split(',')
            elif (self.yearMatchRe.match(line)):
                # 当前年份信息
                rltStartYear, _ = self.timeExtractRe.findall(line)[0][1:-1].split(',')

        print('合并课程信息中...(3/3)')

        # 这里需要注意，如果当前validweek放不下js会新建一个课程信息对象把validweek补到前面去
        # 这个课程信息对象需要特殊处理，否则日期会摆放不正确（这里和jxxt网上处理有一定差异！）
        # 因此如果发现这种情况需要特殊处理,如果碰到两个课程信息只有validweeks不同就需要进行合并这两个validweeks
        # 进行课程信息合并
        for curCourse in unMergedCouseList:
            needMergeIndicator = 53 - (int(rltAllOccupyWeek) - 1) - int(
                rltAllEndWeek)  # 表示在当前validweeks字符串中还缺多少位，这个值<0表示需要与其他项合并

            if ('1' in curCourse.validweeks[0:int(rltAllOccupyWeek) - 1] and curCourse.validweeks[0] == '0'):
                # 特殊情况，这种情况下前面有1但是第一位是0，这表示当前周次需要转换后才能输出(前面补52个0)
                curCourse.validweeks = (53 - 1) * '0' + curCourse.validweeks

            if curCourse.courseId not in unMergedCourseDict:
                unMergedCourseDict[curCourse.courseId] = [curCourse]
            else:
                merged = False
                if needMergeIndicator < 0:
                    # 说明需要curCourse和其他项合并周次才完整
                    for existCIndex, existingCourse in enumerate(unMergedCourseDict[curCourse.courseId]):
                        if existingCourse.canMergeValidWeek(curCourse):
                            # print('Merge', curCourse.courseName, existingCourse.validweeks, '+', curCourse.validweeks)

                            # 判断一下Merge先后顺序
                            if '1' in curCourse.validweeks[0:int(rltAllOccupyWeek) - 1]:
                                merged = True
                                existingCourse.mergeValidWeek(curCourse.validweeks)
                                unMergedCourseDict[curCourse.courseId][existCIndex] = existingCourse
                                break
                            elif '1' in existingCourse.validweeks[0:int(rltAllOccupyWeek) - 1]:
                                merged = True
                                curCourse.mergeValidWeek(existingCourse.validweeks)
                                unMergedCourseDict[curCourse.courseId][existCIndex] = curCourse
                                break
                            else:
                                raise MyException(ErrorCode.API_CHANGED,
                                                  'API有改变，无法合并课程' + curCourse.courseName + ',请联系作者！')
                if not merged:
                    unMergedCourseDict[curCourse.courseId].append(curCourse)

        for curCourseList in unMergedCourseDict.values():
            rltCouseList.extend(curCourseList)

        return rltStartYear, rltAllOccupyWeek, rltAllStartWeek, rltAllEndWeek, rltCouseList


def cvt2Caldav(startYear: str, allOccupyWeek: str, allStartWeek: str, allEndWeek: str, courseList: list, alarmTime: int,
               modifyDEFTime: bool, splitCourse: bool, icsFileName: str):
    """
    将课程信息转换为.ics日历文件
    :param startYear: 课表年份，可以通过SuesApi.getCourseTable获得
    :param allOccupyWeek: 教学活动起始周,可以通过SuesApi.getCourseTable获得 从1计数
    :param allStartWeek: 教学活动起始周,是相对allOccupyWeek的值,一般为1,可以通过SuesApi.getCourseTable获得 从1计数
    :param allEndWeek: 教学活动结束周,是相对allOccupyWeek的值,可以通过SuesApi.getCourseTable获得 从1计数
    :param courseList: 课程信息列表，可以通过SuesApi.getCourseTable获得
    :param alarmTime: 提前提醒分钟数，正整数
    :param modifyDEFTime 是否修正DEF楼课程第三节和第四节的时间
    :param splitCourse: 是否将横跨的课程按照1-4节 5-8节 9-14节切分
    :param icsFileName: ics文件的名称
    """
    uid = 1

    cal = Calendar()
    weekExtractRe = re.compile(r'[1]+')

    # 自动识别第一周的日期（第一天从周日开始）

    # 下面日期中周日是第一天，从0计数，而curCourse.day认为周一是第一天需要-1

    # 特殊年份在python中第0周和第1周相同,需往后顺延一周才能得到正确日期
    offset = 0
    if datetime.strptime(startYear + '-01-01', "%Y-%m-%d") \
            .replace(tzinfo=tz.gettz('Beijing')).weekday() is 6:
        offset = 1

    firstWeekTime = datetime.strptime(''.join([str(startYear), '-W', str(int(allOccupyWeek) - 1 + offset), '-0']),
                                      "%Y-W%U-%w") \
        .replace(tzinfo=tz.gettz('Beijing'))

    print('\n教学活动范围：%s周-%s周' % (allStartWeek, allEndWeek))

    # 按照用户的选择切分整块的日程
    splitedCourseList = []
    if not splitCourse:
        splitedCourseList = courseList
    else:
        for curCourse in courseList:
            bucket1_4 = []
            bucket5_8 = []
            bucket9_14 = []
            for i in curCourse.courses:
                i = int(i)
                if i <= 3:
                    bucket1_4.append(str(i))
                elif 4 <= i <= 7:
                    bucket5_8.append(str(i))
                else:
                    bucket9_14.append(str(i))
            if len(bucket1_4) > 0:
                cache = copy.copy(curCourse)
                cache.courses = bucket1_4
                splitedCourseList.append(cache)
            if len(bucket5_8) > 0:
                cache = copy.copy(curCourse)
                cache.courses = bucket5_8
                splitedCourseList.append(cache)
            if len(bucket9_14) > 0:
                cache = copy.copy(curCourse)
                cache.courses = bucket9_14
                splitedCourseList.append(cache)

    for curCourse in splitedCourseList:
        # 遍历开课时间段，每个开课时间段（周次）对应课程表上的一个格子，创建一个日程

        for validweek in weekExtractRe.finditer(curCourse.validweeks):
            curCourseBegWeek = validweek.start() - (int(allOccupyWeek) - 1)  # 当前课程第一次开课周次 从0计数
            curCourseEndWeek = (validweek.end() - 1) - (int(allOccupyWeek) - 1)  # 当前课程最后第一次开课周次  从0计数

            courseTimes = sorted([int(time) for time in curCourse.courses],
                                 key=cmp_to_key(cmp_courseTime))  # 排序，找到对应的上课下课时间

            begTime = courseTimes[0]  # 当前课程开课当天的上课时间
            endTime = courseTimes[-1]  # 当前课程开课当天的下课时间
            timeModified = False
            if modifyDEFTime and curCourse.roomName[0] in ['D', 'E', 'F'] and begTime in [2, 3] and endTime in [2, 3]:
                begTime = DEFtimeTable[begTime][0]  # D、E、F楼上课时间
                endTime = DEFtimeTable[endTime][-1]  # D、E、F楼下课时间
                timeModified = True
            else:
                begTime = timetable[begTime][0]  # 上课时间
                endTime = timetable[endTime][-1]  # 下课时间
            begTime = begTime.split(':')
            endTime = endTime.split(':')

            # 整合上面运算得到的上下课时间
            startDayFrom = firstWeekTime \
                           + timedelta(weeks=int(curCourseBegWeek)) \
                           + timedelta(days=(int(curCourse.day) + 1) % 7) \
                           + timedelta(hours=int(begTime[0]), minutes=int(begTime[1]))

            startDayTo = firstWeekTime \
                         + timedelta(weeks=int(curCourseBegWeek)) \
                         + timedelta(days=(int(curCourse.day) + 1) % 7) \
                         + timedelta(hours=int(endTime[0]), minutes=int(endTime[1]))

            untilDay = firstWeekTime \
                       + timedelta(weeks=int(curCourseEndWeek)) \
                       + timedelta(days=(int(curCourse.day) + 1) % 7) \
                       + timedelta(hours=int(endTime[0]), minutes=int(endTime[1]))

            # 调试信息输出
            print('正在添加日程： %23s\t%23s\t%d-%d周\t星期%d %d-%d节\t%s' % (
                curCourse.courseName,
                curCourse.teacherName,
                int(curCourseBegWeek) + 1,
                int(curCourseEndWeek) + 1,
                int(curCourse.day) + 1,
                int(courseTimes[0]) + 1,
                int(courseTimes[-1]) + 1,
                curCourse.roomName), end='')
            if (timeModified):
                print('\tDEF楼 3 4节时间调整')
            else:
                print('\t')

            event = Event()
            # 必须保证UID在本日历内唯一，否则某些日历不能导入
            event.add('uid', curCourse.courseId + curCourse.roomId + curCourse.day +
                      str(curCourseBegWeek) + str(curCourseEndWeek) + str(courseTimes[0]) + str(courseTimes[-1]))
            uid += 1
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
------------------------------------------------------------------------------
 ____  _   _ _____ ____      ____ ____   ____   _____           _ 
/ ___|| | | | ____/ ___|    / ___|___ \ / ___| |_   _|__   ___ | |
\___ \| | | |  _| \___ \    \___ \ __) | |       | |/ _ \ / _ \| |
 ___) | |_| | |___ ___) |    ___) / __/| |___    | | (_) | (_) | |
|____/ \___/|_____|____/    |____/_____|\____|   |_|\___/ \___/|_|   Ver 1.1

SUES 课表转iCalendar日程工具 by XtTech 
源代码/Issue/贡献 https://github.com/GammaPi/SUES-S2C-Tool 如果觉得好用别忘记Star哦！
------------------------------------------------------------------------------''')

    # 1.get captha
    try:
        print('连接Github获取更新提示中(3秒)...', end='')
        try:
            print('成功!')
            r = requests.get('https://raw.githubusercontent.com/GammaPi/SUES-S2C-Tool/master/Notification.txt',
                             timeout=3)
            print('\n', r.text, sep='')
        except requests.exceptions.RequestException as e:  # This is the correct syntax
            print('')
            print('无法获取Github上的更新提示，建议您查看项目主页以确保软件最新，以免导出出错。')

        suesApi = SuesApi()
        print('测试http://jxxt.sues.edu.cn是否能正常访问(10秒)...', end='')
        suesApi.newSession()
        print('连接成功!')

        username = input('\n请输入学号:')
        passwd = input("\n请输入密码:")

        print('\n获取验证码中,验证码将在另外窗口中弹出...')
        capthaBytes = suesApi.getCaptha()
        i = Image.open(BytesIO(capthaBytes))
        i = i.resize((i.size[0] * 4, i.size[1] * 4))
        i.show('验证码')

        captcha = input('\n请输入验证码(图片另弹窗口):')

        suesApi.login(username, passwd, captcha)

        yearList = suesApi.getYears()
        print('')
        for i, year in enumerate(yearList):
            print(i + 1, ':', year)
        yearSelection = int(input('请选择导出课表的范围:'))
        if not (0 < yearSelection and yearSelection <= len(yearList)):
            raise MyException(ErrorCode.INPUT_ERROR, '时间段输入不正确,请输入冒号左边的序号')
        yearSelection = yearList[yearSelection - 1]

        termList = suesApi.getTerms(yearSelection)
        termList = sorted([int(term) for term in termList])
        print('')
        for i, term in enumerate(termList):
            print(i + 1, ':第%d学期' % term)
        termSelection = int(input('请选择学期:'))
        if not (0 < termSelection and termSelection <= len(termList)):
            raise MyException(ErrorCode.INPUT_ERROR, '学期输入不正确,请输入冒号左边的序号')
        termSelection = str(termList[termSelection - 1])

        modifyDefTime = input('\n是否要将D E F楼 3-4节课的下课时间从 10:05-11:35 调整到 10:25-11:55 y/n:')
        if modifyDefTime == 'y':
            modifyDefTime = True
        elif modifyDefTime == 'n':
            modifyDefTime = False
        else:
            raise MyException(ErrorCode.INPUT_ERROR, '是否修改D E F时间输入不正确，如果要修改输入y，否则输入n')

        splitLargeEvent = input('\n是否将教学管理系统中跨越中、晚休息时间的日程拆分开来记录?\n选择拆分使得部分日程变得更琐碎，但更易读 y/n:')
        if splitLargeEvent == 'y':
            splitLargeEvent = True
        elif splitLargeEvent == 'n':
            splitLargeEvent = False
        else:
            raise MyException(ErrorCode.INPUT_ERROR, '输入不正确，如果要拆分输入y，否则输入n')

        print('')
        startYear, allOccupyWeek, allStartWeek, allEndWeek, couseList = suesApi.getCourseTable(yearSelection,
                                                                                               termSelection)

        alarmTime = input('\n请输入课前提醒分钟数(0-120):')
        if (alarmTime.isdigit() and 0 <= int(alarmTime) and int(alarmTime) <= 120):
            alarmTime = int(alarmTime)
        else:
            raise MyException(ErrorCode.INPUT_ERROR, '提醒时间只能为上课前0-120分钟')

        fileName = ''.join([username, '_', yearSelection, '学年_第', termSelection, '学期 课表导出.ics'])
        cvt2Caldav(startYear, allOccupyWeek, allStartWeek, allEndWeek, couseList,
                   alarmTime, modifyDefTime, splitLargeEvent, fileName)
        print('\n上面的内容是为了方便您与教学管理系统课表进行核对，产学合作等不在课表上的课程不会被添加！')
        print('日历生成好了，快去导入吧！ ics文件位置在本程序根目录下，文件名为:' + fileName)
    except MyException as e:
        print('\n[异常]', e, file=sys.stderr)
        if DBG_MODE:
            raise e
    except KeyboardInterrupt as e:
        print('\nKeyboardInterrupt')
    except BaseException as e:
        print('\n[异常] 遇到未识别的异常，可能因为BUG或教学系统API变化导致，非常抱歉，请更新软件或联系开发者！\n 错误信息：' + str(e), file=sys.stderr)
        if DBG_MODE:
            raise e

    input('\n按回车键退出')
