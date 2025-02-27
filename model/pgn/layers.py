import tensorflow as tf, numpy as np, time
from utils.textprocessing import load_embedding_matrix


class Encoder(tf.keras.layers.Layer):
    def __init__(self, vocab_size, embedding_dim, enc_units, batch_sz, rnn_type='gru', bidirectional=False,
                 embedding_matrix=None):
        '''

        :param vocab_size: The size of vocabulary list
        :param embedding_dim: The length of embedding vectors
        :param enc_units: The number of rnn units in encoder
        :param batch_sz: Batch size
        :param rnn_type: The type of recurrent neural network we used
        :param bidirectional: Whether our neural network is bidirectional
        :param embedding_matrix: If provided, we will use provided pretrained embedding matrix.
        Otherwise, we will train embedding matrix by ourselves.
        '''
        super(Encoder, self).__init__()
        self.vocab_size = vocab_size
        self.embedding_dim = embedding_dim
        self.enc_units = enc_units
        self.batch_sz = batch_sz
        self.rnn_type = rnn_type
        self.bidirectional = bidirectional

        self.embedding_weights = [embedding_matrix] if embedding_matrix is not None else None
        trainalbe = False if embedding_matrix is not None else True
        self.embedding = tf.keras.layers.Embedding(vocab_size, embedding_dim, weights=self.embedding_weights,
                                                   trainable=trainalbe)

        if self.rnn_type.lower() == 'gru':
            self.rnn = tf.keras.layers.GRU(self.enc_units, return_sequences=True, return_state=True)
        elif self.rnn_type.lower() == 'lstm':
            self.rnn = tf.keras.layers.LSTM(self.enc_units, return_sequences=True, return_state=True)
        else:
            raise Exception('Only GRU and LSTM are supported now.')

        if self.bidirectional:
            self.rnn = tf.keras.layers.Bidirectional(self.rnn)

    def call(self, x, hidden):
        '''

        :param x: input sequence
        :param hidden: initial state for our network
        :return:
        1. output sequence which contains output of every units
        2. state generated by last unit
        '''
        x = self.embedding(x)

        if self.rnn_type.lower() == 'gru':
            if self.bidirectional:
                output, forward_state, backward_state = self.rnn(x, initial_state=hidden)
                state = [forward_state, backward_state]
            else:
                output, state = self.rnn(x, initial_state=hidden)
        elif self.rnn_type.lower() == 'lstm':
            if self.bidirectional:
                output, forward_h, forward_c, backward_h, backward_c = self.rnn(x, initial_state=hidden)
                state = [forward_h, forward_c, backward_h, backward_c]
            else:
                output, state_h, state_c = self.rnn(x, initial_state=hidden)
                state = [state_h, state_c]
        else:
            raise Exception('Only GRU and LSTM are supported now.')

        return output, state

    def initialize_hidden_state(self, batch_size=None):
        batch_size = self.batch_sz if batch_size is None else batch_size
        if self.rnn_type.lower() == 'gru':
            initial_hidden = tf.zeros((batch_size, self.enc_units))
            if self.bidirectional:
                initial_hidden = [initial_hidden, initial_hidden]
        elif self.rnn_type.lower() == 'lstm':
            dim = tf.zeros((batch_size, self.enc_units))
            if self.bidirectional:
                initial_hidden = [dim, dim, dim, dim]
            else:
                initial_hidden = [dim, dim]
        else:
            raise Exception('Only GRU and LSTM are supported now.')

        return initial_hidden


class Decoder(tf.keras.layers.Layer):
    def __init__(self, vocab_size, embedding_dim, dec_units, batch_sz, rnn_type='gru', embedding_matrix=None):
        '''

        :param vocab_size: The size of vocabulary list
        :param embedding_dim: The length of embedding vectors
        :param dec_units: The number of rnn units in decoder
        :param batch_sz: Batch size
        :param rnn_type: The type of recurrent neural network we used
        '''
        super(Decoder, self).__init__()
        self.vocab_size = vocab_size
        self.embedding_dim = embedding_dim
        self.dec_units = dec_units
        self.batch_sz = batch_sz
        self.rnn_type = rnn_type

        self.embedding_weights = [embedding_matrix] if embedding_matrix is not None else None
        trainalbe = False if embedding_matrix is not None else True
        self.embedding = tf.keras.layers.Embedding(vocab_size, embedding_dim, weights=self.embedding_weights, trainable=trainalbe)

        if self.rnn_type.lower() == 'gru':
            self.rnn = tf.keras.layers.GRU(self.dec_units, return_sequences=True, return_state=True)
        elif self.rnn_type.lower() == 'lstm':
            self.rnn = tf.keras.layers.LSTM(self.dec_units, return_sequences=True, return_state=True)
        else:
            raise Exception('Only GRU and LSTM are supported now.')

        self.dense1 = tf.keras.layers.Dense(2 * self.dec_units)
        self.dense2 = tf.keras.layers.Dense(self.vocab_size, activation='softmax')

    def call(self, x, hidden, context_vector):
        '''

        :param x: output from decoder unit of previous time step
        :param hidden: state from decoder unit of previous time step
        :param context_vector: context vector calculated by attention layer
        :return: output and state generated by current unit
        '''
        x = self.embedding(x)

        if self.rnn_type.lower() == 'gru':
            output, state = self.rnn(x, initial_state=hidden)
        elif self.rnn_type.lower() == 'lstm':
            output, state_h, state_c = self.rnn(x, initial_state=hidden)
            state = [state_h, state_c]
        else:
            raise Exception('Only GRU and LSTM are supported now.')

        output = tf.concat([tf.expand_dims(context_vector, 1), output], axis=-1)

        output = tf.reshape(output, shape=(-1, output.shape[2]))
        output = self.dense1(output)
        output = self.dense2(output)

        return output, state


class Attention(tf.keras.layers.Layer):
    def __init__(self, units=0, score_type='additive-concat', mask_index=None):
        '''

        :param units: The number of units in some dense layers.
        Only used when score type is additive-concat or general
        :param score_type: The type of attention score we used
        '''
        super(Attention, self).__init__()
        self.units = units
        self.score_type = score_type
        self.mask_index = mask_index
        self.va = tf.keras.layers.Dense(1)
        self.w1 = tf.keras.layers.Dense(self.units)
        self.w2 = tf.keras.layers.Dense(self.units)
        self.w3 = tf.keras.layers.Dense(self.units)

    def attention_score(self, enc_output, curr_dec_hidden, encoder_pad_mask, prev_coverage=None):
        '''

        :param enc_output: encoder output we used to calculate
        :param curr_dec_hidden: decoder state we used to calculate
        :param encoder_pad_mask: a list to determine whether a position is a padding
        :param prev_coverage: coverage score generated by last time step
        :return: attention score
        '''
        if self.score_type.lower() == 'additive-concat':
            if prev_coverage is None:
                e = self.va(tf.nn.tanh(self.w1(enc_output) + self.w2(curr_dec_hidden)))
            else:
                e = self.va(tf.nn.tanh(self.w1(enc_output) + self.w2(curr_dec_hidden) + self.w3(prev_coverage)))
        elif self.score_type.lower() == 'dot-product':
            e = tf.expand_dims(tf.reduce_sum(enc_output * curr_dec_hidden, axis=-1), -1)
        elif self.score_type.lower() == 'general':
            temp_vector = self.w1(curr_dec_hidden)
            e = tf.expand_dims(tf.reduce_sum(enc_output * temp_vector, axis=-1), -1)
        elif self.score_type.lower() == 'cosine-similarity':
            enc_output_norm = tf.nn.l2_normalize(enc_output, -1)
            dec_hidden_norm = tf.nn.l2_normalize(curr_dec_hidden, -1)
            cos_similarity = tf.reduce_sum(enc_output_norm * dec_hidden_norm, axis=-1)
            e = tf.expand_dims(cos_similarity, -1)

        mask = tf.cast(encoder_pad_mask, dtype=e.dtype)
        masked_e = tf.squeeze(e, axis=-1) * mask
        masked_e = tf.expand_dims(masked_e, axis=2)
        attention_score = tf.nn.softmax(masked_e, axis=1)
        return attention_score

    def call(self, dec_hidden, enc_output, encoder_pad_mask, use_coverage, prev_coverage):
        '''

        :param dec_hidden: decoder state we used to generate context vector
        :param enc_output: encoder output we used to generate context vector
        :param encoder_pad_mask: a list to determine whether a position is a padding
        :param use_coverage: whether to use coverage to calculate attention score
        :param prev_coverage: coverage score generated by last time step
        :return: context vector, attention weights and coverage score
        '''
        curr_dec_hidden = tf.expand_dims(dec_hidden, 1)
        # start = time.time()
        if use_coverage:
            attention_score = self.attention_score(enc_output, curr_dec_hidden, encoder_pad_mask, prev_coverage=prev_coverage)
            converage = attention_score if prev_coverage is None else (prev_coverage + attention_score)
        else:
            attention_score, e = self.attention_score(enc_output, curr_dec_hidden, encoder_pad_mask, prev_coverage=None)
            converage = None
        # duration = time.time() - start
        # print(duration)
        context_vector = tf.reduce_sum(attention_score * enc_output, axis=1)

        return context_vector, tf.squeeze(attention_score, -1), converage


class Pointer(tf.keras.layers.Layer):

    def __init__(self):
        super(Pointer, self).__init__()
        self.w_s_reduce = tf.keras.layers.Dense(1)
        self.w_i_reduce = tf.keras.layers.Dense(1)
        self.w_c_reduce = tf.keras.layers.Dense(1)

    def call(self, context_vector, dec_hidden, dec_inp):
        '''

        :param context_vector: context vector calculated by attention layer
        :param dec_hidden: decoder state we used to generate context vector
        :param dec_inp: decoder input generated by last time step
        :return: Pgen score
        Notes: Pgen score = sigmoid(ws * dec_hidden + wc * context_vector + wi * dec_input)
        '''
        return tf.nn.sigmoid(self.w_s_reduce(dec_hidden) + self.w_c_reduce(context_vector) + self.w_i_reduce(dec_inp))



if __name__ == '__main__':
    # initiate required params and load saved embedding matrix
    rnn_type = 'lstm'
    bidirectional = True
    score_type = 'cosine-similarity'
    input_length = 4
    output_length = 3
    embedding_matrix, _ = load_embedding_matrix()
    vocab_size = len(embedding_matrix)
    embedding_dim = len(embedding_matrix[0])
    encoder_units = 10
    batch_size = 5
    if score_type == 'additive-concat':
        decoder_units = encoder_units
        attention_units = encoder_units
    elif score_type == 'general':
        decoder_units = encoder_units
        if bidirectional:
            attention_units = 2 * encoder_units
        else:
            attention_units = encoder_units
    else:
        attention_units = 0
        if bidirectional:
            decoder_units = 2 * encoder_units
        else:
            decoder_units = encoder_units

    # construct different layers
    encoder = Encoder(vocab_size, embedding_dim, encoder_units, batch_size, rnn_type=rnn_type,
                      embedding_matrix=embedding_matrix, bidirectional=bidirectional)
    attention = Attention(attention_units, score_type=score_type)
    decoder = Decoder(vocab_size, embedding_dim, decoder_units, batch_size, rnn_type=rnn_type,
                      embedding_matrix=embedding_matrix)

    # initiate start vectors
    x = np.random.randint(0, vocab_size, size=(batch_size, input_length)).astype(np.float32)
    hidden = encoder.initialize_hidden_state()
    enc_output, enc_state = encoder(x, hidden)

    dec_hidden = tf.convert_to_tensor(np.zeros((batch_size, decoder_units)).astype(np.float32))
    if rnn_type == 'gru':
        dec_state = dec_hidden
    else:
        dec_state = [dec_hidden, dec_hidden]
    dec_prediction = np.random.randint(0, vocab_size, size=(batch_size, 1))
    predictions = []

    # emulate one step
    for i in range(3):
        context_vector = attention(dec_hidden, enc_output)
        dec_output, dec_state = decoder(dec_prediction, dec_state, context_vector)
        if rnn_type.lower() == 'lstm':
            dec_hidden = dec_state[0]
        else:
            dec_hidden = dec_state
        dec_prediction = tf.math.argmax(dec_output, 1)
        predictions.append(dec_prediction.numpy())
        dec_prediction = tf.expand_dims(dec_prediction, 1)
    print(np.array(predictions).shape)

