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
    heartbeatTimer: null,
    currentHeartbeat: null,

    formID: 'theasForm',  //Value for id attribute of the Theas form
    lastError: null,

    origDialogTitle: null,
    origDialogContent: null,

    isReady: false,
    onReady: null,
    pendingAsyncs: [],

    ready: function (func) {
        let that = this;

        that.onReady = func;
        if (that.isReady) {
            that.onReady(that);
        }
    },


    //Utility function to translate a date provided by SQL into a javascript date
    dateFromSQLString: function (s) {
        let d = null;

        if (s.trim().length) {
            let bits = s.split(/[-T:]/g);
            d = new Date(bits[0], bits[1] - 1, bits[2]);
            if (bits[3] + bits[4] + bits[5] > 0) {
                d.setHours(bits[3], bits[4], bits[5]);
            }
        }

        return d;
    },

    uuidv4: function () {
      return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
        (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
      )
    },

    isBase64: function(buf) {
        // see:  https://stackoverflow.com/a/43279141
        const notBase64 = /[^A-Z0-9+\/=]/i;

        if (typeof buf != 'string') {
            return false;
        }

        const len = buf.length;
        if (!len || len % 4 !== 0 || notBase64.test(buf)) {
        return false;
        }

        const firstPaddingChar = buf.indexOf('=');
        return firstPaddingChar === -1 ||
        firstPaddingChar === len - 1 ||
        (firstPaddingChar === len - 2 && buf[len - 1] === '=');
    },

    //Utility function to format a date provided by SQL
    mdyFromSQLString: function (s) {
        let that = this;

        let thisDate = that.dateFromSQLString(s);
        let thisDateStr = '';
        if (thisDate) {
            thisDateStr = thisDate.getMonth() + 1 + "/" + thisDate.getDate() + "/" + thisDate.getFullYear();
        }
        return thisDateStr;
    },

    //Return a theas control.  Optionally, sets the value of the control first.
    get: function (ctrlName, newValue, persist=true) {
        let that = this;

        let thisCtrl;

        if (ctrlName) {

            if (ctrlName.indexOf('theas:') !== 0 && persist) {
                ctrlName = 'theas:' + ctrlName;
            }

            let thisForm = $('#' + that.formID);
            thisCtrl = thisForm.find('*[name="' + ctrlName + '"]');

            if (thisCtrl.length === 0) {
                thisCtrl = null;
            }

            if (typeof newValue !== 'undefined') {
                if (!thisCtrl) {
                    //auto-create a new control
                    let $thForm = $('#' + th.formID);
                    if ($thForm && $thForm.length) {
                        $thForm.append($('<input name="' + ctrlName + '" type="hidden" />'));
                    }

                    thisCtrl = $('*[name="' + ctrlName + '"]');

                }

                if (thisCtrl) {
                    thisCtrl.val(newValue);
                }
            }
        }

        return thisCtrl;
    },

    getval: function (ctrlName, newValue, persist=true) {
        let that = this;
        let thisCtrl = that.get(ctrlName, newValue, persist);
        let thisVal;

        if (thisCtrl) {
            thisVal = thisCtrl.val();
        }
        return thisVal;
    },

    setval: function (ctrlName, newValue, persist=true) {
        let that = this;
        return that.get(ctrlName, newValue, persist);
    },

    //Update all theas controls as per the updateStr
    updateAll: function (updateStr) {
        let that = this;
        let buf = '';

        if (that.isBase64(updateStr)) {
            buf = atob(updateStr)
        }
        else {
            buf = updateStr
        }


        // updateStr might be either URL-encoded name/value pairs or JSON
        // if JSON, we are looking for theasParams
        try {
            let jsonBuf = JSON.parse(buf);
            let theasDict = jsonBuf['theasParams'];
            // https://stackoverflow.com/a/34913701 and https://stackoverflow.com/a/45731301
            Object.keys(theasDict).forEach(function(key) {
                th.setval(key, theasDict[key]);
            });

        }
        catch {
            try {
                let that = this;
                let nv;
                if (buf) {
                    buf = buf.split('&');
                    for (let i = 0; i < buf.length; i++) {
                        nv = buf[i].split('=');
                        if (nv[0]) {
                            that.get(decodeURIComponent(nv[0]), decodeURIComponent(nv[1]));
                        }
                    }
                }
            } catch (e) {
                th.get('th:ErrorMessage', 'TH.updateAll could not parse the TheasParams update string.');
                that.haveError(true);
            }
        }

    },


    //Encode value of all Theas controls
    encodeAll: function () {
        $('*[name^="theas:"]').each(function (index) {
            let that = this;
            let $that = $(that);
            $that.val(encodeURIComponent($that.val()));
        });
    },


    //Decode value of all Theas controls
    decodeAll: function (namePrefix) {
        if (!namePrefix) {
            namePrefix = 'theas:';
        }
        $('[name^="' + namePrefix + '"]').each(function (index) {
            let that = this;
            let $that = $(that);
            $that.val(decodeURIComponent($that.val()));
        });
    },


    //Serialize all theas controls into a string
    serialize: function () {
        let that = this;
        let buf = '';

        $('*[name^="theas:"]').each(function (index) {
            let that = this;
            let $that = $(that);
            buf = buf + $that.attr('name') + '=' + encodeURIComponent($that.val()) + '&';
        });

        return buf;
    },

    //Clear all theas controls and cookies
    clearAll: function () {
        let that = this;

        $('*[name^="theas:"]').each(function (index) {
            let $that = $(that);
            $that.val('');
        });
    },


    //Simple error handler for Async errors
    receiveAsyncError: function (thisjqXHR, thisStatus, thisError) {
        let that = this;

        let debug = 0;

        if (debug) {
            alert('Error waiting for async response: ' + thisStatus);
        }

        //that.clearAll();
        //that.sendAsync('logout');
        //window.location = '/'
        that.raiseError('Error waiting for async response: ' + thisjqXHR.status.toString() + ' (' + thisjqXHR.statusText + ')');
    },


    //Default function for onReceive of Async response
    receiveAsync: function (dataReceived, status) {
        //Server will send one body of data.  Technically, this can be anything:  XML, JSON, URL-encoded
        //name-value pairs, binary data, etc.  Theas expects that the default is simply URL-encoded
        //name-value pairs, and that the pairs provided  are theas controls.

        //To support receiving a different type of data, simply pass sendAsync a different function for
        //onSuccess.

        let debug = 0;

        if (debug === 1) {
            alert(dataReceived);
        }

        if (dataReceived) {
            if (dataReceived === 'invalidSession') {
                this.thisConfig.thisTheas.raiseError('Async response indicates invalidSession');
            } else if (dataReceived === 'sessionOK') {
                let noop = null
            } else {
                this.thisConfig.thisTheas.updateAll(dataReceived);
            }
        }

        if (!this.thisConfig.thisTheas.haveError(true)){
            if (this.afterSuccess) {
              this.afterSuccess(dataReceived, this.thisConfig);
            }
        }

    },

    //Send Async request
    sendAsync: function (cmd,
                         config = {
                             onAfterSuccess: null,
                             onSuccess: null,
                             thisURL: 'async',
                             origEvent: null,
                             timeout: null
                             },
                          dataToSend,
                          onSuccess
                         ) {
        let that = this;

        if (!config) {
            config = {};
        }

        if (config.origEvent) {
            //for convenience:  we don't want the button click to submit the form.
            config.origEvent.preventDefault();
        }

        let buf = '';

        if (cmd) {
            buf = buf + 'cmd=' + cmd + '&';
        }

        buf = buf + '_xsrf=' + $('input[name="_xsrf"]').val() + '&' + that.serialize();

        if (dataToSend) {
            buf = buf + '&' + dataToSend + '&';
        }

        if (!config.thisUrl) {
            config.thisUrl = 'async';
        }

        if (onSuccess) {
            // respect positional parameter for backwards-compabibility,
            // but coppy function reference to config.onSuccess
            config.onSuccess = onSuccess;
        }

        if (!config.onSuccess) {
            config.onSuccess = that.receiveAsync;
        }

        config.requestID = that.uuidv4();
        config.dateStart = Date.now();
        config.thisTheas = that;

        $.ajax({
            url: config.thisUrl,
            type: 'POST',
            cache: false,
            timeout: config.timeout, //30000,
            dataType: 'text',
            contentType: 'application/x-www-form-urlencoded; charset=UTF-8',
            data: buf,
            thisConfig : config,
            success: config.onSuccess,
            afterSuccess: config.onAfterSuccess,
        });

    },

    beforeSubmit: function () {
        let that = this;

        let isOK = true;

        th.get('th:PerformUpdate', '1');

        //explicitly validate each focusable form element to trigger display of hints
        $('#' + that.formID).find('input[type!="hidden"],select,textarea').each(function (i, el) {
            if (!el.checkValidity()) {
                $(el).addClass('is-invalid');
                $(el).parent('label').addClass('is-invalid');
            } else {
                $(el).removeClass('is-invalid');
                $(el).parent('label').removeClass('is-invalid');
            }
        });


        //explicitly verify that the whole form is valid before submitting
        if ($('#' + that.formID)[0].checkValidity()) {
            // encode all form values
            // actually...we can trust the browser to encode before performing the submit
            //that.encodeAll();

            isOK = true;
        } else {
            that.get('th:ErrorMessage', 'One or more fields have incomplete or invalid values.');
            that.haveError(true);
            isOK = false;
        }

        $('#' + that.formID).addClass('was-validated');

        return isOK;
    },

    submitForm: function (e, performUpdate) {
        let that = this;

        let isOK = true;

        if (e === true) {
            // set the Theas param th:PerformUpdate to tell the server it should treat this
            // form post as an update request
            e = null;
            performUpdate = true;
        }

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
            } else {
                //IE8 and Lower
                e.cancelBubble = true;
            }
        }

        if (performUpdate) {
            isOK = that.beforeSubmit();
        }

        if (isOK) {
            $('#' + that.formID).submit();
        }

        return isOK;
    },

    receiveHeartbeat: function(dataReceived, thisConfig) {
        let that = this;

        //call the onHeartbeat function
        thisConfig.onHeartbeat(dataReceived, thisConfig);

        // clear the currentHeartbeat flag
        thisConfig.thisTheas.currentHeartbeat = null;

        // schedule our next beat
        thisConfig.thisTheas.sendHeartbeat(thisConfig.onHeartbeat, thisConfig.thisTheas.heartbeatSeconds);
    },

    sendHeartbeat: function (onHeartbeat, delaySeconds) {
        let that = this;

        if (delaySeconds || delaySeconds == 0) {
            that.heartbeatSeconds = delaySeconds;
            that.heartbeatTimer = window.setTimeout(
                (function () {
                    if (!that.currentHeartbeat || (Date.now() - that.currentHeartbeat > 60)) {
                        // If there is a pending request, and it is not "stuck" (not more than 60
                        // seconds old), we don't want to send another heartbeat.
                        that.currentHeartbeat = Date.now();
                        that.sendHeartbeat(onHeartbeat);
                    }
                }),
                that.heartbeatSeconds * 1000
            );
        }
        else {
            that.sendAsync('heartbeat',
                           {
                           onAfterSuccess: that.receiveHeartbeat,
                           onHeartbeat: onHeartbeat
                           },
                           that.serialize()
            );
        }
    },

    sync: function () {
        let that = this;
        that.sendAsync('theasParams');
    },

    getModal: function () {
        // Retrieves (or creates) modal.  If needed, set certain defaults and performs a push to the modalStack.
        // Note that there is only ONE modal, and this gets modified as needed each time it is displayed.
        // Attributes for the modal (body, header, buttons, et al) are pushed to the modalStack each time
        // the modal is displayed, and are popped when the modal is hidden. In this way this single
        // modal can be used even in nested showModal calls.
        let that = this;

        // make sure the modal exists
        let $thMsgDlg = $('#thMsgModal');

        if (!$thMsgDlg.length) {
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
                '<button type="button" id="btnCloseThMsg" class="btn btn-primary close">Close</button>' +
                '</div>' +
                '</div>' +
                '</div>' +
                '</div>');
        }

        $thMsgDlg = $('#thMsgModal');

        if ($thMsgDlg.length) {
            if (typeof $thMsgDlg.data('modalStack') == 'undefined' || $thMsgDlg.data('modalStack').length === 0) {
                let origState = {
                    msg: $thMsgDlg.find('.modal-body').html(),
                    title: $thMsgDlg.find('.modal-title').text(),
                    onClose: null,
                    goBackOnClose: false,
                    skipDefaultClose: false,
                    focusedElem: document.activeElement,
                    buttonHtml: $thMsgDlg.find('.modal-footer').html()
                };

                $thMsgDlg.data('modalStack', []);

                $thMsgDlg.data('modalStack').push(origState);

                $thMsgDlg.data('doOnClose', origState.onClose);
                $thMsgDlg.data('goBackOnClose', origState.goBackOnClose);
                $thMsgDlg.data('skipDefaultClose', origState.skipDefaultClose);
                $thMsgDlg.data('focusedElem', origState.focusedElem);
                //$thMsgDlg.find('.modal-footer').html(prevState.buttonHtml)

                // define onClick handler for modal close button
                $thMsgDlg.find('button.close').click(function () {
                    $thMsgDlg.modal('hide');
                });

                // define onClose handler
                $thMsgDlg.on('hidden.bs.modal', function (e) {
                    e.stopPropagation();

                    if (typeof $thMsgDlg.data('doOnClose') == 'function') {
                        if (!$(e).hasClass('cancel')){
                            $thMsgDlg.data('doOnClose')($thMsgDlg);
                        }
                    }

                    if (!$thMsgDlg.data('skipDefaultClose')) {
                        $thMsgDlg.modal('hide');

                        // navigate back in history if applicable
                        if ($thMsgDlg.data('goBackOnClose') && window.history.length) {
                            window.history.back();
                        } else {
                            let $origFocused = $($thMsgDlg.data('focusedElem'));
                            if ($origFocused.length > 0){
                                $origFocused.focus();
                            }
                        }
                    }

                    if ($thMsgDlg.data('modalStack').length > 1) {
                        $thMsgDlg.data('modalStack').pop();
                    }

                    let prevState = $thMsgDlg.data('modalStack')[$thMsgDlg.data('modalStack').length-1];

                    $thMsgDlg.find('.modal-body').html(prevState.msg);
                    $thMsgDlg.find('.modal-title').text(prevState.title);

                    $thMsgDlg.data('doOnClose', prevState.onClose);
                    $thMsgDlg.data('goBackOnClose', prevState.goBackOnClose);
                    $thMsgDlg.data('skipDefaultClose', prevState.skipDefaultClose);
                    $thMsgDlg.data('focusedElem', prevState.focusedElem);
                    $thMsgDlg.find('.modal-footer').html(prevState.buttonHtml);

                });
            }

        } else {
            $thMsgDlg = null;
        }

        return $thMsgDlg;
    },

    showModal: function (msg, title, onClose, goBackOnClose, skipDefaultClose) {
        let that = this;
        let $thMsgDlg = that.getModal();

        if (!$thMsgDlg.length) {
            throw 'Modal not found.'
        }

        let newState = {
            msg: msg,
            title: title,
            onClose: onClose,
            goBackOnClose: goBackOnClose,
            skipDefaultClose: false,
            focused: document.activeElement,
            buttonHtml: $thMsgDlg.data('modalStack')[0].buttonHtml
        };

        $thMsgDlg.data('modalStack').push(newState);

        $thMsgDlg.find('.modal-body').html(newState.msg);
        $thMsgDlg.find('.modal-title').text(newState.title);

        $thMsgDlg.data('doOnClose', newState.onClose);
        $thMsgDlg.data('goBackOnClose', newState.goBackOnClose);
        $thMsgDlg.data('skipDefaultClose', newState.skipDefaultClose);
        $thMsgDlg.data('focusedElem', newState.focusedElem);
        $thMsgDlg.find('.modal-footer').html(newState.buttonHtml);

        $thMsgDlg.modal('show');

        return $thMsgDlg
    },

    raiseError: function (errMsg) {
        let that = this;
        that.get('th:ErrorMessage', errMsg);
        that.haveError(true);
    },

    haveError: function (showModal, backOnError, onClose) {
        let that = this;

        if (typeof showModal == 'undefined') {
            //set default value
            showModal = true;
        }

        if (typeof backOnError == 'undefined') {
            //set default value
            backOnError = false;
        }

        let haveError = false;

        if (that.get('th:ErrorMessage')) {
            let thErrorMsg = that.get('th:ErrorMessage').val();
            if (thErrorMsg) {
                haveError = true;
                th.lastError = thErrorMsg;

                // clear the error message
                that.get('th:ErrorMessage', '');
                that.sendAsync('clearError');

                if (showModal) {
                    let msgParts = thErrorMsg.split('|');
                    let msgTitle = 'Error';
                    if (msgParts.length > 1) {
                        msgTitle = msgParts[1];
                        thErrorMsg = msgParts[0];
                    }
                    th.showModal(thErrorMsg, msgTitle, onClose, backOnError);
                }

            }
        }
        return haveError;
    }

};
th = TH;

$(document).ready(function () {
    // Decode all Theas control values
    th.decodeAll();
    $('._thControl').css('visibility', 'visible');
    th.isReady = true;
    if (th.onReady) {
        th.onReady(th);
    }
});
