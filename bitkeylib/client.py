import os
import bitkey_pb2 as proto
import random

def show_message(message):
    print "MESSAGE FROM DEVICE:", message
    
def show_input(input_text, message=None):
    if message:
        print "QUESTION FROM DEVICE:", message
    return raw_input(input_text)

class CallException(Exception):
    pass

class PinException(CallException):
    pass

class OtpException(CallException):
    pass

class BitkeyClient(object):
    
    def __init__(self, transport, debuglink=None,
                 algo=proto.BIP32, 
                 message_func=show_message, input_func=show_input, debug=False):
        self.transport = transport
        self.debuglink = debuglink
        
        self.algo = algo
        self.message_func = message_func
        self.input_func = input_func
        self.debug = debug
        
        self.setup_debuglink()
        self.init_device()
    
    def _get_local_entropy(self):
        return os.urandom(32)
        
    def init_device(self):
        self.master_public_key = None
        self.session_id = ''.join([ chr(random.randrange(0, 255, 1)) for _ in xrange(0, 16) ])
        self.features = self.call(proto.Initialize(session_id=self.session_id))
        self.UUID = self.call(proto.GetUUID())
        
    def get_master_public_key(self):
        if self.master_public_key:
            return self.master_public_key
        
        self.master_public_key = self.call(proto.GetMasterPublicKey(algo=self.algo)).key
        return self.master_public_key
        
    def get_address(self, n):
        return self.call(proto.GetAddress(algo=self.algo, address_n=n)).address
        
    def get_entropy(self, size):
        return self.call(proto.GetEntropy(size=size)).entropy
    
    def _pprint(self, msg):
        return "<%s>:\n%s" % (msg.__class__.__name__, msg)

    def setup_debuglink(self, button=None, pin_correct=False, otp_correct=False):
        self.debug_button = button
        self.debug_pin = pin_correct
        self.debug_otp = otp_correct
        
    def call(self, msg):
        if self.debug:
            print '----------------------'
            print "Sending", self._pprint(msg)
        
        self.transport.write(msg)
        resp = self.transport.read_blocking()

        if isinstance(resp, proto.ButtonRequest):
            if self.debuglink and self.debug_button:
                print "Pressing button", self.debug_button
                self.debuglink.press_button(self.debug_button)
            
            return self.call(proto.ButtonAck())   
                
        if isinstance(resp, proto.OtpRequest):
            if self.debuglink:
                otp = self.debuglink.read_otp()
                if self.debug_otp:
                    msg2 = otp
                else:
                    msg2 = proto.OtpAck(otp='__42__')
            else:
                otp = self.input_func("OTP required: ", resp.message)
                msg2 = proto.OtpAck(otp=otp)
            
            return self.call(msg2)   
    
        if isinstance(resp, proto.PinRequest):
            if self.debuglink:
                pin = self.debuglink.read_pin()
                if self.debug_pin:
                    msg2 = pin
                else:
                    msg2 = proto.PinAck(pin='__42__')
            else:
                pin = self.input_func("PIN required: ", resp.message)
                msg2 = proto.PinAck(pin=pin)
                
            return self.call(msg2)
        
        if isinstance(resp, proto.Failure):
            self.message_func(resp.message)
            
            if resp.code == 3:
                raise OtpException("OTP is invalid")
                
            elif resp.code == 4:    
                raise CallException("Action cancelled by user")
                
            elif resp.code == 6:
                raise PinException("PIN is invalid")
                
            raise CallException(resp.code, resp.message)
        
        if self.debug:
            print "Received", self._pprint(resp)
            
        return resp

    def get_uuid(self):
        return self.call(proto.GetUUID()).UUID
        
    def sign_tx(self, inputs, outputs):
        '''
            inputs: list of TxInput
            outputs: list of TxOutput
            
            proto.TxInput(index=0,
                          address_n=0,
                          amount=0,
                          prev_hash='',
                          prev_index=0,
                          #script_sig=
                          )
            proto.TxOutput(index=0,
                          address='1Bitkey',
                          #address_n=[],
                          amount=100000000,
                          script_type=proto.PAYTOADDRESS,
                          #script_args=
                          )                                      
        '''
    
        # Prepare and send initial message
        tx = proto.SignTx()
        tx.algo = self.algo # Choose BIP32 or ELECTRUM way for deterministic keys
        tx.random = self._get_local_entropy() # Provide additional entropy to the device
        tx.inputs_count = len(inputs)
        tx.outputs_count = len(outputs)            
        res = self.call(tx)

        # Prepare structure for signatures
        signatures = [None]*len(inputs)
        
        while True:
            if isinstance(res, proto.OutputRequest):
                res = self.call(outputs[res.request_index])
                continue
            
            if isinstance(res, proto.InputRequest):
                if res.signed_index >= 0:
                    print "!!! SIGNED INPUT"
                    signatures[res.signed_index] = res.signature
        
                if res.request_index >= 0:
                    print "REQUESTING", res.request_index    
                    res = self.call(inputs[res.request_index])
                    continue
                
                # There was no request for another input,
                # so we're done!
                break
            
            if isinstance(res, proto.Failure):
                raise CallException("Signing failed")
        
        return signatures
                
        #print "PBDATA", tx.SerializeToString().encode('hex')
        
        #################
        #################
        #################
        
        '''
        signatures = [('add550d6ba9ab7e01d37e17658f98b6e901208d241f24b08197b5e20dfa7f29f095ae01acbfa5c4281704a64053dcb80e9b089ecbe09f5871d67725803e36edd', '3045022100dced96eeb43836bc95676879eac303eabf39802e513f4379a517475c259da12502201fd36c90ecd91a32b2ca8fed2e1755a7f2a89c2d520eb0da10147802bc7ca217')]
        
        s_inputs = []
        for i in range(len(inputs)):
            addr, v, p_hash, p_pos, p_scriptPubKey, _, _ = inputs[i]
            pubkey = signatures[i][0].decode('hex')
            sig = signatures[i][1].decode('hex')
            s_inputs.append((addr, v, p_hash, p_pos, p_scriptPubKey, pubkey, sig))
        
        return s_inputs
                
        s_inputs = []
        for i in range(len(inputs)):
            addr, v, p_hash, p_pos, p_scriptPubKey, _, _ = inputs[i]
            private_key = ecdsa.SigningKey.from_string( self.get_private_key(addr, password), curve = SECP256k1 )
            public_key = private_key.get_verifying_key()
            pubkey = public_key.to_string()
            tx = filter( raw_tx( inputs, outputs, for_sig = i ) )
            sig = private_key.sign_digest( Hash( tx.decode('hex') ), sigencode = ecdsa.util.sigencode_der )
            assert public_key.verify_digest( sig, Hash( tx.decode('hex') ), sigdecode = ecdsa.util.sigdecode_der)
            s_inputs.append( (addr, v, p_hash, p_pos, p_scriptPubKey, pubkey, sig) )
        return s_inputs
        '''

    def reset_device(self):
        resp = self.call(proto.ResetDevice(random=self._get_local_entropy()))
        self.init_device()
        return isinstance(resp, proto.Success)
    
    def load_device(self, seed, otp, pin, spv):
        resp = self.call(proto.LoadDevice(seed=seed, otp=otp, pin=pin, spv=spv))
        self.init_device()
        return isinstance(resp, proto.Success)        