TH = {
        /* EXAMPLES:

        //On click of a button, update some Theas form field values, then submit the form 
        $('#btnSubmit').click(function (){
            th('PerformUpdate', '1');
            th('EmployerJob:JobDescription', $('#editor').cleanHtml());
            th('th:NextPage', 'pageCreateEmployerAccountStep2');
            $('#jobForm').submit();
        });
        */
        
        ////////////////////////////////////////////////////
       heartbeatCommand : 'heartbeat',
       heartbeatInterval : 30000,
       formID : 'theasForm',  //Value for id attribute of the Theas form

        //Utility function to translate a date provided by SQL into a javascript date
        dateFromSQLString: function(s) {
            var d = null;

            if (s.trim().length) {
                var bits = s.split(/[-T:]/g);
                d = new Date(bits[0], bits[1] - 1, bits[2]);
                if (bits[3] + bits[4] + bits[5] > 0) {
                    d.setHours(bits[3], bits[4], bits[5]);
                }
            }

            return d;
        },

        //Utility function to format a date provided by SQL
        mdyFromSQLString: function (s) {
            var thisDate = this.dateFromSQLString(s);
            var thisDateStr = '';
            if (thisDate) {
                thisDateStr = thisDate.getMonth() + 1 + "/" + thisDate.getDate() + "/" + thisDate.getFullYear();
            }
            return thisDateStr;
        },

        //Return a theas control.  Optionally, sets the value of the control first.
        get: function (ctrlName, newValue) {
            if (ctrlName.indexOf('theas:') != 0) {
                ctrlName = 'theas:' + ctrlName;
            }

            var thisCtrl = $('*[name="' + ctrlName + '"]');

            if (thisCtrl.length == 0) {
                thisCtrl = null;
            }
            else if (newValue) {
                thisCtrl.val(newValue);
            }

            return thisCtrl;
        },

    
        //Update all theas controls as per the updateStr
        updateAll: function (updateStr) {
            var q = updateStr;
            var hash;
            if (q != undefined) {
                q = q.split('&');
                for (var i = 0; i < q.length; i++) {
                    hash = q[i].split('=');
                    th.get(hash[0], decodeURIComponent(hash[1]));
                }
            }
        },
    

        //Encode value of all Theas controls
        encodeAll: function() {
            $('*[name^="theas:"]').each(function( index ){
                var $this = $(this);                
                $this.val( encodeURIComponent($this.val()) );
            });
        },


        //Decode value of all Theas controls
        decodeAll: function() {
            $('*[name^="theas:"]').each(function( index ){
                var $this = $(this);
                $this.val( decodeURIComponent($this.val()) );
            });
        },

    
        //Serialize all theas controls into a string
        serialize: function () {
            var buf = '';

            $('*[name^="theas:"]').each(function( index ){
                var $this = $(this);
                buf = buf + $this.attr('name') + '=' + encodeURIComponent($this.val()) + '&';
            });

            return buf;
        },
    
        //Clear all theas controls and cookies
        clearAll: function() {
            $('*[name^="theas:"]').each(function( index ){
                var $this = $(this);                
                $this.val('');
            });
        },
    

        //Simple error handler for Async errors
        receiveAsyncError: function (thisjqXHR, thisStatus, thisError) {
            var debug = 0;

            if (debug) {
                alert('Error waiting for async response: ' + thisStatus);
            }

            //th.clearAll();
            //th.sendAsync('logout');
            window.location = '/'
        },

    
        //Default function for onReceive of Async response
        receiveAsync: function (dataReceived){
            //Server will send one body of data.  Technically, this can be anything:  XML, JSON, URL-encoded
            //name-value pairs, binary data, etc.  Theas expects that thte default is simply URL-encoded 
            //name-value pairs, and that the pairs provided  are theas controls.
            
            //To support receiving a different type of data, simply pass sendAsync a different function for
            //onSuccess.
            var debug = 0;
            
            if (debug == 1){
                alert(dataReceived);
            }
            
            if (dataReceived) {
                if (dataReceived == 'invalidSession'){
                    window.location = '/'
                }
                else if (dataReceived == 'sessionOK'){
                    var noop = null
                }
                else {
                    th.updateAll(dataReceived);
                }
            }
        },

    
        //Send Async request
        sendAsync: function (cmd, origevent, dataToSend, onSuccess, thisUrl) {
            if (origevent) {
                //for convenience:  we don't want the button click to submit the form.
                origevent.preventDefault();
            }

            var buf = '';
            
            if (cmd) {
                buf = buf + 'cmd=' + cmd + '&';
            }
            
            buf = buf + '_xsrf=' + $('input[name="_xsrf"]').val() + '&' + th.serialize();
            
            if (dataToSend) {
                buf = buf + '&' +  dataToSend + '&';
            }

            if (!onSuccess) {
                onSuccess = th.receiveAsync;
            }

            if (!thisUrl){
                thisUrl = 'async';
            }

            $.ajax({
                url: thisUrl,
                type: 'POST',
                cache: false,
                timeout: null, //30000,
                dataType: 'text',
                contentType: 'application/x-www-form-urlencoded; charset=UTF-8',
                //context: workTimer,
                data: buf,
                success: onSuccess,
                error: th.receiveAsyncError
            });
        },
        
        
        submitForm: function(e) {
            if (!e) {
                e = window.event;
            }

            if (e.preventDefault) {
                e.preventDefault();
            }

            if (e.stopPropagation) {
                //IE9 & Other Browsers
                e.stopPropagation();
            }
            else {
                //IE8 and Lower
                e.cancelBubble = true;
            }

            // encode all form values
            th.encodeAll();

            $('#' + th.formID).submit();

            return false;
        },
    
        initHeartbeat: function(interval) {
            th.heartbeatInterval = interval;
            window.setInterval(
                (function () {
                    th.sendAsync(th.heartbeatCommand);
                }),
                th.heartbeatInterval)
        }
};
th=TH;
