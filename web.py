from flask import Flask, render_template, jsonify, session, request
import pymysql.cursors
import sqlite3

import requests
from bs4 import BeautifulSoup
import re
import sys
from datetime import datetime

import threading

app = Flask(__name__)

dbfile = 'devicemart.db'

webpassword = ''

userid = ''
password = ''

lastrefresh = 0

# generate a secret key
import os
app.secret_key = os.urandom(24)


@app.route('/')
def main():
	return render_template('devicemart.html')

@app.route('/data')
def data():
	conn = pymysql.connect(host='localhost', user=dbuser, password=dbpassword, database=dbname, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)

	c = conn.cursor()
	c.execute("SELECT UNIX_TIMESTAMP(`date`)*1000 AS `d`, ROUND(AVG(`h`),2) AS `h`, ROUND(AVG(`t`),2) AS `t` FROM `ht` GROUP BY UNIX_TIMESTAMP(`date`) DIV 300 ORDER BY `id` DESC LIMIT 2016;")
	ht = c.fetchall()

	return jsonify(ht)

def updater():
	s = requests.Session()
	conn = sqlite3.connect(dbfile)

	c = conn.cursor()

	try:
		c.execute('CREATE TABLE `orders`(`id` INTEGER PRIMARY KEY AUTOINCREMENT, `date` DATETIME, `orderid` BIGINT, `productid` BIGINT, `name` TEXT, `count` INTEGER, `unitprice` INTEGER, `price` INTEGER, `received` BOOLEAN);')
	except:
		pass
	conn.commit()

	sys.stderr.write('login...\n')
	loginpage = 'https://www.devicemart.co.kr/login_process/login'
	data = {
		'return_url': '/mypage/order_catalog',
		'order_auth': '1',
		'userid': userid,
		'password': password
	}
	r = s.post(loginpage, data=data)

	if 'parent.document.location' in r.text:
		sys.stderr.write('login succeed...\n')
	else:
		sys.stderr.write('login failed.\n')
		exit()

	sys.stderr.write('retrieving order list...\n')

	r = s.get('https://www.devicemart.co.kr/mypage/order_catalog')
	soup = BeautifulSoup(r.text, 'html.parser')
	pages = soup.find(class_='paging_navigation')

	page_len = len(pages.find_all('a'))
	sys.stderr.write('found {} pages...\n'.format(page_len))

	for i in range(0, page_len):
		sys.stderr.write('fetching a page\n')
		order = s.get('https://www.devicemart.co.kr/mypage/order_catalog?page={}&popup=&iframe='.format(i+1))
		soup = BeautifulSoup(order.text, 'html.parser')
		list_order = soup.find_all('div', class_='orde_catawarp')
		neworder = True
		for j in list_order:
			orderid = re.search(r'no=([0-9]+)', j.find_all('span')[0].find('a')['href']).group(1)
			c.execute('SELECT * FROM `orders` WHERE `orderid`=?;', (orderid,))
			if c.fetchone() != None:
				sys.stderr.write('skipping order #{}\n'.format(orderid))
				neworder = False
				break
			sys.stderr.write('fetching order number {}\n'.format(orderid))
			singleorder = s.get('https://www.devicemart.co.kr/mypage/order_view?no={}'.format(orderid))
			soup = BeautifulSoup(singleorder.text, 'html.parser')
			date = datetime.strptime(orderid[0:8], '%Y%M%d').strftime('%Y-%M-%d')
			itemtable = soup.find(class_='list_table_style').find('tbody')
			items = str(itemtable).split('<td class="left">')
			sys.stderr.write('{} items found\n'.format(len(items)-1))
			for k in items[1:]:
				name = re.search(r'target="_blank">([^<]+)</', k)
				count = int(re.search(r'<td>([0-9]+)', k).group(1))
				if name != None:
					productid = re.search(r'no=([0-9]+)', k).group(1)
				if name == None:
					name = re.search(r'class="order_name">([^<]+)</', k)
					productid = '-1'
				if name == None:
					name = re.search(r'</span>([^<]+)</dd', k)
					productid = '-2'
				name = name.group(1)
				name = name.strip()
				price = int(re.search(r'class="right">([^<]+)원</', k).group(1).replace(',', ''))
				c.execute('INSERT INTO `orders`(`date`, `orderid`, `productid`, `name`, `count`, `unitprice`, `price`, `received`) VALUES (?,?,?,?,?,?,?,FALSE);', (date, orderid, productid, name, count, price//count, price))
				conn.commit()
		if neworder == False:
			sys.stderr.write('no more new order...exit\n')
			break

@app.route('/update')
def update():
	global lastrefresh
	if not 'auth' in session:
		return jsonify({'result': False})
	if datetime.now().timestamp() - lastrefresh < 60:
		return jsonify({'result': False})
	lastrefresh = datetime.now().timestamp()
	t = threading.Thread(target=updater)
	t.start()
	return jsonify({'result': True})

@app.route('/search/')
@app.route('/search/<string:text>')
def search(text=''):
	if not 'auth' in session:
		return jsonify({'result': False})
	text = text.replace(' ', '%');
	text = '%'+text+'%'
	conn = sqlite3.connect(dbfile)
	c = conn.cursor()
	c.execute("SELECT *,ROUND(`unitprice`*0.1)*`count`,ROUND(`unitprice`*1.1)*`count` FROM `orders` WHERE `date` LIKE ? OR `orderid` LIKE ? OR `name` LIKE ? ORDER BY `orderid` DESC;", (text, text, text))
	data = c.fetchall()
	c.execute("SELECT SUM(`productid`), SUM(`unitprice`), SUM(`count`), SUM(`price`), SUM(ROUND(`unitprice`*0.1)*`count`), SUM(ROUND(`unitprice`*1.1)*`count`) FROM `orders` WHERE `date` LIKE ? OR `orderid` LIKE ? OR `name` LIKE ?;", (text, text, text))
	sum_ = c.fetchone()
	c.execute("SELECT `name`, SUM(`unitprice`), SUM(`count`), SUM(`price`), SUM(ROUND(`unitprice`*0.1)*`count`), SUM(ROUND(`unitprice`*1.1)*`count`) FROM `orders` WHERE `productid`=-1 GROUP BY `name`;")
	magazine = c.fetchall()
	data = list(map(lambda x: x[0:2]+(str(x[2]),)+x[3:], data))
	data = {'result': True, 'data': data, 'sum': sum_, 'magazine': magazine}
	return jsonify(data)

@app.route('/auth', methods=['POST'])
def auth():
	if request.form['password'] == webpassword:
		session['auth'] = True
		return jsonify({'result': True})
	print('begin update')
	return jsonify({'result': False})

@app.route('/check/<int:_id>/<int:status>')
def button(_id, status):
	conn = sqlite3.connect(dbfile)
	c = conn.cursor()
	c.execute("UPDATE `orders` SET `received`=? WHERE `id`=?;", (1-status, _id))
	conn.commit()
	return 'ok'

if __name__ == "__main__":
	app.run()