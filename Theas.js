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
       lastError : null,

       origDialogTitle : null,
       origDialogContent : null,

       isReady : false,
       onReady : null,

       ready: function(func){
         this.onReady = func;
         if (this.isReady){
             this.onReady(this);
         }
       },


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


           if (typeof newValue !== 'undefined') {
                if (!thisCtrl) {
                    //auto-create a new control
                    var $thForm = $('#' + th.formID);
                    if ($thForm && $thForm.length) {
                        $thForm.append($('<input name="' + ctrlName + '" type="hidden" />'));
                    }

                    thisCtrl = $('*[name="' + ctrlName + '"]');

                }

                if (thisCtrl) {
                    thisCtrl.val(newValue);
                }
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
                    this.get(hash[0], decodeURIComponent(hash[1]));
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

            //this.clearAll();
            //this.sendAsync('logout');
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
                    this.updateAll(dataReceived);
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

            buf = buf + '_xsrf=' + $('input[name="_xsrf"]').val() + '&' + this.serialize();

            if (dataToSend) {
                buf = buf + '&' +  dataToSend + '&';
            }

            if (!onSuccess) {
                onSuccess = this.receiveAsync.bind(this);
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
                error: this.receiveAsyncError.bind(this)
            });
        },


        submitForm: function(e) {
            if (!e) {
                e = window.event;
            }

            if (e) {
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
            }


            // encode all form values
            // actually...we can trust the browser to encode before performing the submit
            //this.encodeAll();



            //explicitly validate each focusable form element to trigger display of hints
            $('#' + this.formID).find('input[type!="hidden"],select,textarea').each(function(i, el){
                if (! el.checkValidity()) {
                  $(el).addClass('validation-error');
                  $(el).parent('label').addClass('validation-error');
                }
                else {
                 $(el).removeClass('validation-error');
                 $(el).parent('label').removeClass('validation-error');
                }
            });


            //explicitly verify that the whole form is valid before submitting
            if ($('#' + this.formID)[0].checkValidity()){
		        $('#' + this.formID).submit();
            }
	        else {
                this.get('th:ErrorMessage', 'One or more fields have incomplete or invalid values.');
                this.haveError(true);
            }

            return false;
        },

        initHeartbeat: function(interval) {
            this.heartbeatInterval = interval;
            window.setInterval(
                (function () {
                    this.sendAsync(this.heartbeatCommand);
                }),
                this.heartbeatInterval)
        },

        getModal: function(thisMsg, thisTitle) {
                    // make sure the modal exists
                    var $thMsgDlg = $('#thMsgModal');

                    if (! $thMsgDlg.length) {
                        $('body').append('<div id="thMsgModal" class="modal fade" role="dialog">' +
                            '<div class="modal-dialog">' +
                            '<div class="modal-content">' +
                            '<div class="modal-header">' +
                            //'<button type="button" class="close" aria-hidden="true">&times;</button>' +
                            '<div id="thMsgTitle" class="modal-title" style="visibility:hidden"></div>' +
                            '</div>' +
                            '<div class="modal-body">' +
                            '<div class="modal-main"></div>' +
                                //HTML here will be replaced with the error message
                            '</div>' +
                            '<div class="modal-footer">' +
                            '<button type="button" id="btnCloseThMsg" class="btn btn-default close">Close</button>' +
                            '</div>' +
                            '</div>' +
                            '</div>' +
                            '</div>');
                    }

                    $thMsgDlg = $('#thMsgModal');

                    if ($thMsgDlg.length) {
                        if (!this.origDialogContent) {
                            var bufBody = $thMsgDlg.find('.modal-main').html();
                            if ($.trim(bufBody)) {
                                this.origDialogContent = bufBody;
                            }
                        }

                        if (!this.origDialogTitle) {
                            var bufTitle = $thMsgDlg.find('.modal-title').text();
                            if ($.trim(bufTitle)) {
                                this.origDialogTitle = bufTitle;
                            }
                        }

                        if (!thisMsg) {
                            thisMsg = this.origDialogContent;
                        }

                        if (!thisTitle) {
                            thisTitle = this.origDialogTitle;
                        }

                        if (thisMsg) {
                            $thMsgDlg.find('.modal-main').html(thisMsg);
                        }

                        if (thisTitle) {
                            $thMsgDlg.find('.modal-title').text(thisTitle);
                        }

                        $thMsgDlg.find('button.close').click(function () {
                                $thMsgDlg.modal('hide');
                        });
                    }
                    else {
                        $thMsgDlg = null;
                    }

                    return $thMsgDlg;
        },

        showModal: function(msg, title, onClose, goBackOnClose) {
          var $thMsgDlg = this.getModal(msg, title);

           // define a custom click handler for the modal's close button
            $thMsgDlg.find('button.close').click(function () {
                doDefaultClose = true;

                if (typeof onClose !== 'undefined') {
                    doDefaultClose = onClose($thMsgDlg);
                    if (typeof skipDefaultClose == 'undefined') {
                        doDefaultClose = true;
                    }
                }

                if (doDefaultClose) {
                    $thMsgDlg.modal('hide');

                    // navigate back in history if applicable
                    if (goBackOnClose && window.history.length) {
                      window.history.back();
                    }
                }

                // Restore original dialog content
                if (this.origDialogContent && (this.origDialogContent != $thMsgDlg.find('.modal-main').html())) {
                  $thMsgDlg.find('.modal-main').html(this.origDialogContent);
                }

                // Restore original dialog title
                if (this.origDialogTitle && (this.origDialogTitle != $thMsgDlg.find('.modal-title').text())) {
                  $thMsgDlg.find('.modal-title').text(this.origDialogTitle);
                           this.origDialogTitle = $thMsgDlg.find('.modal-title').text(thisTitle);
                }
            });

            $thMsgDlg.modal('show');

        },

        haveError: function(showModal, backOnError, onClose) {
            if (typeof showModal == 'undefined') {
                //set default value
                showModal = true;
            }

            if (typeof backOnError == 'undefined') {
                //set default value
                backOnError = false;
            }

            var haveError = false;

            if (this.get('th:ErrorMessage')) {
                var thErrorMsg = this.get('th:ErrorMessage').val();
                if (thErrorMsg) {
                    haveError = true;
                    th.lastError = thErrorMsg;

                    // clear the error message
                    this.get('th:ErrorMessage', '');
                    this.sendAsync('clearerror');

                    if (showModal) {
                        th.showModal(thErrorMsg, 'Error', onClose, backOnError);
                     }

                }
            }
            return haveError;
        }

};
th=TH;

$(document).ready(function () {
    // Decode all Theas control values
    th.decodeAll();
    $('._thControl').css('visibility', 'visible');
    th.isReady = true;
    if (th.onReady){
        th.onReady(th);
    }
});