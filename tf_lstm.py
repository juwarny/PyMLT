# -*- encoding: utf-8 -*-
from __future__ import print_function
import pandas as pd
import numpy as np
import sqlite3
from sklearn.externals import joblib
from sklearn.preprocessing import StandardScaler
import os, sys
from etaprogress.progress import ProgressBar
os.environ['TF_CPP_MIN_LOG_LEVEL']='3'
import tensorflow as tf
import glob


class TensorflowRegressorLSTM():
    def __init__(self, h_size, s_date):
        prev_bd = int(s_date[:6])-1
        prev_ed = int(s_date[9:15])-1
        if prev_bd%100 == 0: prev_bd -= 98
        if prev_ed%100 == 0: prev_ed -= 98
        pred_s_date = "%d01_%d01" % (prev_bd, prev_ed)
        self.prev_model = '../model/tf/lstm/%s' % pred_s_date
        self.model_dir = '../model/tf/lstm/%s' % s_date
        #The network recieves a frame from the game, flattened into an array.
        #It then resizes it and processes it through four convolutional layers.
        # Create two variables.
        tf.reset_default_graph()

        self.num_epoch = 2
        self.lr = tf.placeholder(dtype=tf.float32)
        self.batch_size = tf.placeholder(dtype=tf.int32)
        self.time_length = tf.placeholder(dtype=tf.int32)

        self.inData =  tf.placeholder(shape=[None, None,23],dtype=tf.float32)
        inReshaped = tf.reshape(self.inData,[self.batch_size, self.time_length,23])

        W1 = tf.Variable(tf.random_normal([h_size,1]))
        b1 = tf.Variable(tf.random_normal([1]))

        lstm_cell = tf.contrib.rnn.BasicLSTMCell(h_size)
        init_state = lstm_cell.zero_state(self.batch_size, dtype=tf.float32)
        outputs, states = tf.nn.dynamic_rnn(\
                lstm_cell, inReshaped, dtype=tf.float32,initial_state=init_state)
        self.pred = tf.matmul(outputs[:,-1], W1) + b1
        
        #Below we obtain the loss by taking the sum of squares difference between the target and prediction Q values.
        self.target = tf.placeholder(shape=[None],dtype=tf.float32)
        error = tf.square(self.target - self.pred)
        self.loss = tf.reduce_mean(error)
        trainer = tf.train.AdamOptimizer(learning_rate=self.lr)
        self.updateModel = trainer.minimize(self.loss)

    def fit(self, X_data, Y_data):
        # Add an op to initialize the variables.
        init_op = tf.global_variables_initializer()
        batch_size = 64
        time_length = 30

        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        lr = 0.0005
        loss_sum = 0
        with tf.Session(config=config) as sess:
            sess.run(init_op)
            if os.path.exists('%s/model.ckpt.meta' % self.prev_model):
                ckpt = tf.train.get_checkpoint_state(self.prev_model)
                saver = tf.train.Saver()
                saver.restore(sess, ckpt.model_checkpoint_path)
            for i in range(self.num_epoch):
                lr *= 0.9
                print("\nEpoch %d/%d is started" % (i+1, self.num_epoch), end='\n')
                bar = ProgressBar(len(X_data)/batch_size, max_width=80)
                for j in range(int(len(X_data)/batch_size)-1):
                    X_batch = X_data[batch_size*j:batch_size*(j+1)].reshape(batch_size, time_length, 23)
                    Y_batch = Y_data[batch_size*j:batch_size*(j+1)]
                    _ = sess.run(self.updateModel, feed_dict={self.lr:lr, self.inData: X_batch, self.target: Y_batch, self.batch_size: 64, self.time_length: time_length})

                    if j%10 == 0:
                        loss = sess.run(self.loss, feed_dict={self.lr:lr, self.inData: X_batch, self.target: Y_batch, self.batch_size: 64, self.time_length: time_length})
                        bar.numerator = j+1
                        loss_sum = ((j/10)*loss_sum + loss)/(j/10+1)
                        print("%s | loss: %f" % (bar, loss_sum), end='\r')
                        sys.stdout.flush()

            if not os.path.exists(self.model_dir):
                os.makedirs(self.model_dir)
            saver = tf.train.Saver()
            save_path = saver.save(sess,'%s/model.ckpt' % self.model_dir)
            print("Model saved in file: %s" % save_path)

    def predict(self, X_data):
        init_op = tf.global_variables_initializer()
        batch_size = 1
        time_length = 30
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        with tf.Session(config=config) as sess:
            sess.run(init_op)
            ckpt = tf.train.get_checkpoint_state(self.model_dir)
            saver = tf.train.Saver()
            saver.restore(sess, ckpt.model_checkpoint_path)
            X_data = X_data.reshape(-1, time_length, 23)
            batch_size = len(X_data)
            return sess.run(self.pred, feed_dict={self.inData: X_data, self.batch_size: batch_size, self.time_length: time_length})


class LstmModel:
    def __init__(self):
        self.data = dict()
        self.frame_len = 30
        self.predict_dist = 5
        self.h_size = 23
        self.scaler = dict()

    def load_all_data(self, begin_date, end_date):
        #con = sqlite3.connect('../data/stock.db')
        #code_list = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        code_list = glob.glob('../data/hdf/*.hdf')
        code_list = list(map(lambda x: x.split('.hdf')[0][-6:], code_list))
        X_data_list, Y_data_list, DATA_list = [0]*10, [0]*10, [0]*10
        idx = 0
        split = int(len(code_list) / 9)
        bar = ProgressBar(len(code_list), max_width=80)
        for code in code_list:
            data = self.load_data(code, begin_date, end_date)
            data = data.dropna()
            len_data = len(data)
            X, Y = self.make_x_y(data, code)
            if len(X) <= 10: continue
            mean_velocity = int(data.loc[len_data-10:len_data,'현재가'].mean()) * int(data.loc[len_data-10:len_data, '거래량'].mean())
            if mean_velocity > 1000000000 or mean_velocity < 10000000: # 10억 이하면 pass
                continue
            code_array = [code] * len(X)
            assert len(X) == len(data.loc[29:len(data)-self.predict_dist-1, '일자'])
            if idx%split == 0:
                X_data_list[int(idx/split)] = list(X)
                Y_data_list[int(idx/split)] = list(Y)
                DATA_list[int(idx/split)] = np.array([data.loc[29:len(data)-6, '일자'].values.tolist(), code_array, data.loc[29:len(data)-6, '현재가'], data.loc[34:len(data), '현재가']]).T.tolist()
            else:
                X_data_list[int(idx/split)].extend(X)
                Y_data_list[int(idx/split)].extend(Y)
                DATA_list[int(idx/split)].extend(np.array([data.loc[29:len(data)-6, '일자'].values.tolist(), code_array, data.loc[29:len(data)-6, '현재가'], data.loc[34:len(data), '현재가']]).T.tolist())
            bar.numerator += 1
            print("%s | %d" % (bar, len(X_data_list[int(idx/split)])), end='\r')
            sys.stdout.flush()
            idx += 1
        print("%s" % bar)

        print("Merge splited data")
        bar = ProgressBar(10, max_width=80)
        for i in range(10):
            if type(X_data_list[i]) == type(1):
                continue
            if i == 0:
                X_data = X_data_list[i]
                Y_data = Y_data_list[i]
                DATA = DATA_list[i]
            else:
                X_data.extend(X_data_list[i])
                Y_data.extend(Y_data_list[i])
                DATA.extend(DATA_list[i])
            bar.numerator = i+1
            print("%s | %d" % (bar, len(DATA)), end='\r')
            sys.stdout.flush()
        print("%s | %d" % (bar, len(DATA)))
        return np.array(X_data), np.array(Y_data), np.array(DATA)

    def load_data(self, code, begin_date, end_date):
        #con = sqlite3.connect('../data/stock.db')
        #df = pd.read_sql("SELECT * from '%s'" % code, con, index_col='일자').sort_index()
        df = pd.read_hdf('../data/hdf/%s.hdf'%code, 'day').sort_index()
        data = df.loc[df.index > str(begin_date)]
        data = data.loc[data.index < str(end_date)]
        data = data.reset_index()
        return data

    def make_x_y(self, data, code):
        data_x = []
        data_y = []
        for col in data.columns:
            try:
                data.loc[:, col] = data.loc[:, col].str.replace('--', '-')
                data.loc[:, col] = data.loc[:, col].str.replace('+', '')
            except AttributeError as e:
                pass
                print(e)
        data.loc[:, 'month'] = data.loc[:, '일자'].str[4:6]
        data = data.drop(['일자', '체결강도'], axis=1)

        # normalization
        data = np.array(data)
        if len(data) <= 0 :
            return np.array([]), np.array([])

        if code not in self.scaler:
            self.scaler[code] = StandardScaler()
            data = self.scaler[code].fit_transform(data)
        elif code not in self.scaler:
            return np.array([]), np.array([])
        else:
            data = self.scaler[code].transform(data)

        for i in range(self.frame_len, len(data)-self.predict_dist+1):
            data_x.extend(np.array(data[i-self.frame_len:i, :]))
            data_y.append(data[i+self.predict_dist-1][0])
        np_x = np.array(data_x).reshape(-1, 23*30)
        np_y = np.array(data_y)
        return np_x, np_y

    def train_model_tensorflow(self, X_train, Y_train, s_date):
        print("training model %s model.cptk" % s_date)
        #model = BaseModel()
        self.estimator = TensorflowRegressorLSTM(self.h_size, s_date)
        self.estimator.fit(X_train, Y_train)
        print("finish training model")

    def evaluate_model(self, X_test, Y_test, orig_data, s_date, fname=None):
        print("Evaluate model test.ckpt")
        self.estimator = TensorflowRegressorLSTM(self.h_size, s_date)
        pred = self.estimator.predict(X_test)
        score = 0
        ratio = [1, 1.01, 1.02, 1.05, 1.1, 1.5, 2, 2.5, 3]
        freq = [0]*len(ratio)
        res = [0]*len(ratio)
        date_min, date_max = 99999999, 0
        assert(len(pred) == len(Y_test))
        pred = np.array(pred).reshape(-1)
        Y_test = np.array(Y_test).reshape(-1)
        for i in range(len(pred)):
            score += (float(pred[i]) - float(Y_test[i]))*(float(pred[i]) - float(Y_test[i]))
        score = np.sqrt(score/len(pred))
        print("score: %f" % score)
        for idx in range(len(pred)):
            buy_price = int(orig_data[idx][2])
            future_price = int(orig_data[idx][3])
            date = int(orig_data[idx][0])
            date_min = min(date_min, date)
            date_max = max(date_max, date)
            try:
                pred_transform = self.scaler[str(orig_data[idx][1])].inverse_transform([pred[idx]] + [0]*22)[0]
                cur_transform = self.scaler[str(orig_data[idx][1])].inverse_transform([X_test[idx][23*29]] + [0]*22)[0]
            except (KeyError, ValueError) as e:
                print(e)
                print(orig_data[idx][1], pred[idx])
                continue
            for j in range(len(ratio)):
                if pred_transform > buy_price * ratio[j]:
                    res[j] += (future_price - buy_price*1.005)*(100000/buy_price+1)
                    freq[j] += 1
                    print("[%s, %d] buy: %6d, sell: %6d, earn: %6d" % (str(date), freq[j], buy_price, future_price, (future_price - buy_price*1.005)*(100000/buy_price)))
        print("date length: %d - %d (%d)" % (date_min, date_max, int(len(pred)/2500)))
        for i in range(len(res)):
            if freq[i] == 0: continue
            print("%5d times trade, ratio: %1.2f, result: %10d (%6d)" %(freq[i], ratio[i], res[i], res[i]/freq[i]))
        if fname is not None:
            fout = open(fname, 'wt')
            fout.write("date length: %d - %d (%d)\n" % (date_min, date_max, int(len(pred)/2500)))
            for i in range(len(res)):
                if freq[i] == 0: continue
                fout.write("%5d times trade, ratio: %1.2f, result: %10d (%6d)\n" %(freq[i], ratio[i], res[i], res[i]/freq[i]))

    def load_current_data(self):
        #con = sqlite3.connect('../data/stock.db')
        #code_list = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        #code_list = list(map(lambda x: x[0], code_list))
        code_list = glob.glob('../data/hdf/*.hdf')
        code_list = list(map(lambda x: x.split('.hdf')[0][-6:], code_list))
        X_test = []
        DATA = []
        first = True
        bar = ProgressBar(len(code_list), max_width=80)
        #for code in code_list:
        while bar.numerator < len(code_list):
            code = code_list[bar.numerator]
            print("%s | %d" % (bar, len(X_test)), end='\r')
            sys.stdout.flush()
            #df = pd.read_sql("SELECT * from '%s'" % code, con, index_col='일자').sort_index()
            df = pd.read_hdf('../data/hdf/%s.hdf'%code, 'day').sort_index()
            data = df.iloc[-30:,:]
            data = data.reset_index()
            for col in data.columns:
                try:
                    data.loc[:, col] = data.loc[:, col].str.replace('--', '-')
                    data.loc[:, col] = data.loc[:, col].str.replace('+', '')
                except AttributeError as e:
                    pass
            data.loc[:, 'month'] = data.loc[:, '일자'].str[4:6]
            data = data.drop(['일자', '체결강도'], axis=1)
            if len(data) < 30:
                code_list.remove(code)
                continue
            DATA.append(int(data.loc[len(data)-1, '현재가']))
            try:
                data = self.scaler[code].transform(np.array(data))
            except KeyError:
                code_list.remove(code)
                continue
            X_test.extend(np.array(data))
            bar.numerator += 1
        X_test = np.array(X_test).reshape(-1, 23*30)
        print()
        return X_test, code_list, DATA

    def make_buy_list(self, X_test, code_list, orig_data, s_date):
        BUY_UNIT = 20000
        print("make buy_list")
        self.estimator = TensorflowRegressor(s_date)
        pred = self.estimator.predict(X_test)
        res = 0
        score = 0
        pred = np.array(pred).reshape(-1)

        # load code list from account
        set_account = set([])
        with open('../data/stocks_in_account.txt', encoding='utf-8') as f_stocks:
            for line in f_stocks.readlines():
                data = line.split(',')
                set_account.add(str(data[6].replace('A', '')))

        buy_item = ["매수", "", "시장가", 0, 0, "매수전"]  # 매수/매도, code, 시장가/현재가, qty, price, "주문전/주문완료"
        with open("../data/buy_list.txt", "wt", encoding='utf-8') as f_buy:
            for idx in range(len(pred)):
                real_buy_price = int(orig_data[idx])
                buy_price = float(X_test[idx][23*29])
                buy_price_transform = self.scaler[code_list[idx]].inverse_transform([buy_price] + [0]*22)[0]
                volume = float(X_test[idx][23*29+1])
                volume_transform = self.scaler[code_list[idx]].inverse_transform([0]*1 + [buy_price] + [0]*21)[1]
                if volume_transform * buy_price_transform < 1000000000: # 하루 거래량이 10억 이하이면 pass
                    continue
                try:
                    pred_transform = self.scaler[code_list[idx]].inverse_transform([pred[idx]] + [0]*22)[0]
                except KeyError:
                    continue
                print("buy_price: %d, real_buy_price: %d" % (buy_price_transform, real_buy_price))
                print("[BUY PREDICT] code: %s, cur: %5d, predict: %5d" % (code_list[idx], real_buy_price, pred_transform))
                if pred_transform > real_buy_price * 1.1 and code_list[idx] not in set_account:
                    print("add to buy_list %s" % code_list[idx])
                    buy_item[1] = code_list[idx]
                    buy_item[3] = int(BUY_UNIT / real_buy_price) + 1
                    for item in buy_item:
                        f_buy.write("%s;"%str(item))
                    f_buy.write('\n')

    def load_data_in_account(self):
        # load code list from account
        DATA = []
        with open('../data/stocks_in_account.txt', encoding='utf-8') as f_stocks:
            for line in f_stocks.readlines():
                data = line.split(',')
                DATA.append([data[6].replace('A', ''), data[1], data[0]])

        # load data in DATA
        #con = sqlite3.connect('../data/stock.db')
        X_test = []
        idx_rm = []
        first = True
        bar = ProgressBar(len(DATA), max_width=80)
        for idx, code in enumerate(DATA):
            bar.numerator += 1
            print("%s | %d" % (bar, len(X_test)), end='\r')
            sys.stdout.flush()

            try:
                #df = pd.read_sql("SELECT * from '%s'" % code[0], con, index_col='일자').sort_index()
                df = pd.read_hdf('../data/hdf/%s.hdf'%code[0], 'day').sort_index()
            except pd.io.sql.DatabaseError as e:
                print(e)
                idx_rm.append(idx)
                continue
            data = df.iloc[-30:,:]
            data = data.reset_index()
            for col in data.columns:
                try:
                    data.loc[:, col] = data.loc[:, col].str.replace('--', '-')
                    data.loc[:, col] = data.loc[:, col].str.replace('+', '')
                except AttributeError as e:
                    pass
                    print(e)
            data.loc[:, 'month'] = data.loc[:, '일자'].str[4:6]
            DATA[idx].append(int(data.loc[len(data)-1, '현재가']))
            data = data.drop(['일자', '체결강도'], axis=1)
            if len(data) < 30:
                idx_rm.append(idx)
                continue
            try:
                data = self.scaler[code[0]].transform(np.array(data))
            except KeyError:
                idx_rm.append(idx)
                continue
            X_test.extend(np.array(data))
        for i in idx_rm[-1:0:-1]:
            del DATA[i]
        X_test = np.array(X_test).reshape(-1, 23*30)
        print()
        return X_test, DATA

    def make_sell_list(self, X_test, DATA, s_date):
        print("make sell_list")
        self.estimator = TensorflowRegressor(s_date)
        pred = self.estimator.predict(X_test)
        res = 0
        score = 0
        pred = np.array(pred).reshape(-1)

        sell_item = ["매도", "", "시장가", 0, 0, "매도전"]  # 매수/매도, code, 시장가/현재가, qty, price, "주문전/주문완료"
        with open("../data/sell_list.txt", "wt", encoding='utf-8') as f_sell:
            for idx in range(len(pred)):
                current_price = float(X_test[idx][23*29])
                current_real_price = int(DATA[idx][3])
                name = DATA[idx][2]
                print("[SELL PREDICT] name: %s, code: %s, cur: %f(%d), predict: %f" % (name, DATA[idx][0], current_price, current_real_price, pred[idx]))
                if pred[idx] < current_price:
                    print("add to sell_list %s" % name)
                    sell_item[1] = DATA[idx][0]
                    sell_item[3] = DATA[idx][1]
                    for item in sell_item:
                        f_sell.write("%s;"%str(item))
                    f_sell.write('\n')
    def save_scaler(self, s_date):
        model_name = "../model/tf/lstm/%s/scaler.pkl" % s_date
        joblib.dump(self.scaler, model_name)

    def load_scaler(self, s_date):
        model_name = "../model/tf/lstm/%s/scaler.pkl" % s_date
        self.scaler = joblib.load(model_name)


if __name__ == '__main__':
    sm = LstmModel()
    X_train, Y_train, _ = sm.load_all_data(20120101, 20160330)
    sm.train_model_tensorflow(X_train, Y_train, "20120101_20160330")
    sm.save_scaler("20120101_20160330")
    sm.load_scaler("20120101_20160330")
    X_test, Y_test, Data = sm.load_all_data(20160301, 20160501)
    sm.evaluate_model(X_test, Y_test, Data, "20120101_20160330")

    #sm.load_scaler("20120101_20170309")
    #X_data, code_list, data = sm.load_current_data()
    #sm.make_buy_list(X_data, code_list, data, "20120101_20170309")
    #X_data, data = sm.load_data_in_account()
    #sm.make_sell_list(X_data, data, "20120101_20170309")
