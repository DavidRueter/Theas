// iOS does not support FormData.set(), so we replace it with FormData.append()
if (!FormData.prototype.set) {
   FormData.prototype.set = FormData.prototype.append;
}

function Theas(vue) {
    // save reference to Vue object that can be used in async callbacks
    this.thatVue = vue;

    this.useCurrentLocation = false;
    this.currentLocation = null;
    this.pendingAsyncs = [];

    Object.defineProperty(
        this,
        'loadingCount',
        {
        get : function loadingCount() {
            // save reference to Theas object
            let thatTheas = this;

            let lc = 0;
            if (thatTheas.pendingAsyncs) {
                lc = thatTheas.pendingAsyncs.length;
            }

            return lc
        }});

}


Theas.prototype.setVue = function (vue){
    this.thatVue = vue;
};


Theas.prototype.uuidv4 = function () {
  return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
    (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
  )
};

Theas.prototype.getCookie = function (cname) {
    // from https://www.w3schools.com/js/js_cookies.asp
    let name = cname + "=";
    let decodedCookie = decodeURIComponent(document.cookie);
    let ca = decodedCookie.split(';');
    for(let i = 0; i <ca.length; i++) {
        let c = ca[i];
        while (c.charAt(0) == ' ') {
            c = c.substring(1);
        }
        if (c.indexOf(name) == 0) {
            return c.substring(name.length, c.length);
        }
    }
    return "";
};


Theas.prototype.objToStr = function (obj, kstr, formObj) {
    // save reference to Theas object
    let thatTheas = this;

    let propdelim = ':';
    let nvdelim = '&';
    let ardelim = ',';

    let buf = '';

    if (!kstr) {
        kstr = '';
    }

    if (Array.isArray(obj)) {
        buf = obj.join(ardelim);
        if (formObj) {
            formObj.set(kstr, buf);
        }
        buf = kstr + '=' + buf;
    }
    else {
        for (let k in obj) {
            if (obj.hasOwnProperty(k)) {
                if (typeof obj[k] == 'object') {
                    buf = buf + (buf == '' ? '' : nvdelim ) + thatTheas.objToStr(obj[k], kstr + (kstr == '' ? '' : propdelim) + k, formObj);
                }
                else {
                    buf = buf + (buf == '' ? '' : nvdelim ) + kstr + (kstr == '' ? '' : propdelim) + k + '=' + obj[k];
                    if (formObj) {
                        formObj.set(kstr + (kstr == '' ? '' : propdelim) + k, obj[k]);
                    }
                }
            }
        }
    }
    return buf
};


Theas.prototype.updateAllTheasParams = function (nv) {
    // nv contains a list of name/value strings
    // generally prepared by splitToNV

    // save reference to Theas object
    let thatTheas = this;

    //Update all theas controls as per the updateStr

    const pfx = 'theas:';
    const pfx2 = 'theas$';

    if (thatTheas.thatVue.theasParams) {
        for (let n in nv) {
            let k;

            if (nv.hasOwnProperty(n)) {
                if (n.startsWith(pfx)) {
                    k = n.substring(pfx.length);
                }
                else if (n.startsWith(pfx2)) {
                    k = n.substring(pfx2.length);
                }

                if (k) {
                    k = k.replace(':', '$');
                    thatTheas.thatVue.theasParams[k] = nv[n];
                }
            }
        }
    }
};


Theas.prototype.splitToNV = function (updateStr) {
    // save reference to Theas object
    let thatTheas = this;

    let outputDict = {};

    //Update all theas controls as per the updateStr

    let isJSON = false;

    let q = updateStr;

    if (q.length > 0) {
        if (q[0] == '{' || q[0] == '[') {
            // looks like response is JSON
            isJSON = true;
        }
    }


    let n;
    let nv;

    if (isJSON) {
        const j = JSON.parse(q);

        for (let k in j) {
            if (j.hasOwnProperty(k)) {
                outputDict[k] = j[k];
            }
        }
    }
    else {
        if (q != undefined) {
            q = q.split('&');
            for (let i = 0; i < q.length; i++) {
                nv = q[i].split('=');

                n = decodeURIComponent(nv[0]);
                outputDict[n] = decodeURIComponent(nv[1]);
            }
        }
    }

    return (outputDict);

};

Theas.prototype.cancelAsync = function (startedBefore) {
    // save reference to Theas object
    let thatTheas = this;

    let i = 0;

    while (i < thatTheas.pendingAsyncs.length) {

        let obj = thatTheas.pendingAsyncs[i];

        if (typeof startedBefore == 'undefined' || startedBefore == null || obj.startTime.isBefore(startedBefore)) {
            // call each cancel function
            thatTheas.pendingAsyncs.splice(i, 1);
            obj.cancelFunc();
        }
        else {
            i++;
        }
    }
};

Theas.prototype.sendAsync = function (config) {
    // save reference to Theas object
    let thatTheas = this;

    if (typeof(config) == 'string') {
        // config actually just contains a string for cmd

        config = {
            url: 'async',
            asyncCmd: config
        }
    }

    // obtain current location
    if (typeof thatTheas.getCurrentLocation === 'function') {
        thatTheas.getCurrentLocation();
    }

    // access the HTML theasForm (that was rendered by the server)
    let theasForm = document.getElementById('theasForm');
    let theasFormData = new FormData(theasForm);

    if (thatTheas.currentLocation) {
        theasFormData.set('theas:th:currentLocation', JSON.stringify(thatTheas.currentLocation));
    }

    if (thatTheas.thatVue.theasParams) {
        // add in values from theas object
        for (let k in thatTheas.thatVue.theasParams) {
            if (thatTheas.thatVue.theasParams.hasOwnProperty(k)) {
                theasFormData.set('theas:' + k.replace('$', ':'), thatTheas.thatVue.theasParams[k]);
            }
        }
    }


    // add in data from config.data if applicable
    if (config && config.data) {
        thatTheas.objToStr(config.data, '', theasFormData);

        for (let n2 in config.data) {
            if (config.data.hasOwnProperty(n2)) {
                theasFormData.set(n2, config.data[n2]);
            }
        }
    }

    if (config && config.asyncCmd) {
        theasFormData.set('cmd', config.asyncCmd);
    }

    theasFormData.set('theas:lastFetch', config.lastFetchDate);

    // Set other form fields such as cmd
    if (thatTheas.currentLocation) {
        theasFormData.set('theas:th:currentLocation', JSON.stringify(thatTheas.currentLocation));
    }


    if (config.binaryFiles && config.binaryFiles.length > 0) {

        for (let i = 0; i < config.binaryFiles.length; i = i + 1) {
            let thisFile = config.binaryFiles[i];
            theasFormData.append(thisFile.fieldName, thisFile.binaryData, thisFile.fileName)
        }
    }

    let requestID = thatTheas.uuidv4();
    let CancelToken = axios.CancelToken;


    let axiosConfig = {
        method: 'post',
        url: config.url,
        data: theasFormData,

        onUploadProgress: config.onUploadProgress,
    };

    if (config && config.binaryFiles && config.binaryFiles.length > 0) {
        axiosConfig.headers = {'Content-Type': 'multipart/form-data'};
    }
    else {
        axiosConfig.headers = {'Content-Type': 'application/x-www-form-urlencoded'};
    }

    const ax = axios.create();
    //note:  passing in axiosConfig to the constructor does NOT work as of 9/7/2018

    /*
     We want to pass in a requestID that will be echoed back in the response handler.
     However the response.config that is passed to the .then() response handler is not
     the original config object, but a copy containing only certain values.  In other
     words, if we simply set axiosConfig.requestID normally, response.config.requestID
     will not actually be present.  See:  https://github.com/axios/axios/issues/520#issuecomment-290988653

     Instead, we need to modify the config just before the request is sent using an
     axios interceptor.

     The requestID is in support of cancellation.  While we could set axiosConfig.cancelToken
     normally (above in the let axiosConfig=...) we instead do that in the interceptor
     as well for the sake of readability / keeping the requestID related code together.
    */
    ax.interceptors.request.use(function (config) {
        config.requestID = requestID;

        config.cancelToken = new CancelToken(function executor(c) {
          // An executor function receives a cancel function as a parameter
          thatTheas.pendingAsyncs.push({startTime: moment(), requestID: requestID, cancelFunc: c});
        });

        return config;
          // IMPORTANT:  must return config, otherwise there are confusing errors.
          // See:  https://github.com/svrcekmichal/redux-axios-middleware/issues/83#issuecomment-407466397
    }, function (error) {
        alert(error);
        return Promise.reject(error);
    });



    ax.request(axiosConfig)
        .then(function (response) {
            // handle success

            // remove cancel entry
            for (let i=0; i < thatTheas.pendingAsyncs.length; i++ ) {
                if (thatTheas.pendingAsyncs[i].requestID == response.config.requestID) {
                    thatTheas.pendingAsyncs.splice(i, 1);
                    break;
                }
            }


            /*
            Removed partial support for non-cookie storage of session tokens on 9/7/2019.
            Cookie-only for the time being.

            // The response may have provided an updated session token in a cookie, in which case
            // we should update our stored value.
            // Note, however, that this does not really work when secure cookies are used, as the
            // server is expecting
            let new_st_cookie = thatTheas.getCookie('theas:th:ST');

            if (thatTheas.thatVue.theasParams['th$ST'] != new_st_cookie) {
                thatTheas.thatVue.theasParams['th$ST'] = new_st_cookie;
            }
            */

            let rd;

            // The server can return whatever it wants in response.data
            // For example, response.data could contain URL-encoded name/value pairs
            // or it contain a JSON string

            if (response.data.length > 0) {

                rd = response.data;

                if (typeof rd === 'string') {
                    let isJSON = false;

                    if (rd.length > 0) {
                        if (rd[0] == '{' || rd[0] == '[') {
                            // looks like response is JSON
                            isJSON = true;
                        }
                    }

                    if (!isJSON) {
                        rd = thatTheas.splitToNV(rd);
                    }
                }

            }

            thatTheas.updateAllTheasParams(rd);

            if (config.onResponse) {
                config.onResponse(rd, response);
            }


            // Optionally, can navigate.

            if (config.onSuccessURL) {
                window.location = config.onSuccessURL;
            }

            /*
             // We could check TheasParams for theas:th:NextPage...but we aren't doing that right now
             // because we don't want to make assumptions about what the Async response data contained.

             let params;
             let nameValue;
             let thisName;
             let thisValue;
             let goToURL = '';

             if (response != undefined) {

             params = response.data.split('&');
             for (let i = 0; i < params.length; i++) {
             nameValue = params[i].split('=');

             thisName = nameValue[0];
             thisValue = nameValue[1];

             if (thisName == 'theas:th:NextPage') {
             // navigate to specified page
             goToURL = thisValue;
             }
             }

             if (!goToURL && config.onSuccessURL) {
             goToURL = config.onSuccessURL;
             }

             if (goToURL) {
             window.location = goToURL;
             }
             }
             */


        })
        .catch(function (response) {
            //handle error
            console.log(response);
        });


};

Theas.prototype.sortArray = function (ar, sortBy, descending) {
    // Utility function to sort an array of objects by the value of a property of the objects.

    // ar: contains an array to be sorted
    // sortBy:  contains a string indicating the property name to sort by
    // descending:  contains a boolean indicating whether sorting should be in descending order

    // save reference to Theas object
    let thatTheas = this;

    ar.sort(function (a, b) {
        let termA;
        let termB;

        if (a.hasOwnProperty(sortBy) && b.hasOwnProperty(sortBy)) {
            if (descending) {
                // intentionally swap A/B, because we want to sort descending
                termB = a[sortBy];
                termA = b[sortBy];
            }
            else {
                termA = a[sortBy];
                termB = b[sortBy];
            }
        }
        else {
            return 0
        }

        if (termA < termB) {
            return -1;
        }
        else if (termA > termB) {
            return 1;
        }
        else {
            return 0;
        }
    });

    return ar
};

Theas.prototype.merge = function () {
    // Utility function to take two or more arrays of objects...each of which that contains a
    // qguid property...and return a new array that contains a list of all unique objects
    // from all arrays.


    // Pass in two or more arrays of objects that contain a uniqueKey property (defaults to 'qguid').
    // Returns a new array that contains a list of all unique objects from all arrays.

    // If the first argument is a string, it is taken to indicate the key containing a unique value that is
    // present in all of the arrays being passed in.  If the first argument is not a string, the uniqueKey
    // key name will be 'qguid'.


    // save reference to Theas object
    let thatTheas = this;

    let ar = [],
        a,
        al = arguments.length,
        firstArParam = 0,
        key,
        thisObj,
        uniqueKey = 'qguid',
        excludeId;


    for (a = 0; a < al; a++) {
        let thisParamType = typeof arguments[a];
        if (thisParamType == 'string' || arguments[a] == null) {
            switch (a) {
                case 0: {
                    // first parameter is a string.  Take that to be the name of the uniqueKey by which we are to merge.
                    uniqueKey = arguments[a];
                    firstArParam++;
                    break;
                }

                case 1: {
                    // second parameter is a string.  Take that to be the ID value that we want to exclude form merge.
                    excludeId = arguments[a];
                    firstArParam++;
                    break;
                }
            }

        }
        else {
            break;
        }

    }

    // loop through all arguments
    for (a = firstArParam; a < al; a++) {
        for (key in arguments[a]) {
            if (arguments[a].hasOwnProperty(key)) {
                thisObj = arguments[a][key];
                if (thisObj.hasOwnProperty(uniqueKey)) {
                    // Note:  object will be omitted if there is no uniqueKey property

                    if (a == firstArParam || thisObj[uniqueKey] != excludeId) {
                        // this is the first array, or we are not supposed to exclude this id
                        ar[thisObj[uniqueKey]] = thisObj;
                    }
                }
            }
        }
    }

    // Note that ar is an associative array with a key of qguid,
    // whereas the arguments had a simple javascript Array ("real" array) with a sequential
    // integer index for the key.  Need to return a "real" array.

    let ar2 = [];

    for (key in ar) {
        if (ar.hasOwnProperty(key)) {
            ar2.push(ar[key]);
        }
    }

    //this.sortArray(ar2);

    return ar2;
};

Theas.prototype.arrayObjToNV = function (aa, nameKey, textKey, nameOutKey, textOutKey, sortBy, descending) {
    // Utility function to take an array of objects and to use it to create a simple name/value array

    // Given any array of arbitrary objects, create a plain array of name-value pairs

    // Some things, notably bootstrap-vue :options attributes for b-form-select components, require
    // a simple javascript array with keys of 'value' and 'text'.

    // Usually JSON data will contain arrays of objects.  This function makes it simple to translate
    // into the needed array.

    //aa:  array of arbitrary objects
    //nameKey:  key of objects in aa to be used for the "name" of the name/value pair
    //textKey:  key of objects in aa to be used for the "value" of the name/value pair
    //nameOutKey:  Optional.  Key to be used in the output array for the "name"  (defaults to "value" for use in :options)
    //textOutKey:  Optional.  Key to be used in the output array for the "value" (defaults to "text" for use in :options)
    //sortBy:  Optional.  If provided, contains a key name in the output array to sort by
    //descending:  Optional.  If sortBy is provided, descending contains a boolean to indicate if sort should be in descending order


    // save reference to Theas object
    let thatTheas = this;

    let ap = [];

    if (!nameOutKey) {
        nameOutKey = 'value';
    }

    if (!textOutKey) {
        textOutKey = 'text';
    }

    for (let thiskey in aa) {
        if (aa.hasOwnProperty(thiskey)) {
            let obj = {};
            obj[nameOutKey] = aa[thiskey][nameKey];
            obj[textOutKey] = aa[thiskey][textKey];
            ap.push(obj);
        }
    }
    if (sortBy) {
        thatTheas.sortArray(ap, sortBy, descending);
    }

    return ap;
};

Theas.prototype.submitForm = function (v, config) {
    // save reference to Theas object
    let thatTheas = this;


    // theasForm contains the HTML theasForm (that was rendered by the server)
    let theasForm = document.getElementById('theasForm');
    let vueObj = v;

    if (vueObj.submitted) {
        return;
    }

    if (vueObj.theasParams) {
        // tell Theas server that we want to update data
        vueObj.theasParams.th$PerformUpdate = '1';
    }


    let theasFormData = {};

    /*
     We want to retrieve the form field values from theasForm and incorporate them into the data we
     will be submitting via Axios.  This should be easy to do:

     let theasFormData = new FormData(theasForm);
     theasFormData.append('someField'. 'someValue');
     theasFormData.set('anotherField', 'anotherValue');

     But Apple / Safari / iOS does not fully support FormData()j or URLSearchParams() , so this does
     not work.

     Instead, we will do this ugly walk through the DOM.
     */

    let i, j;
    for (i = theasForm.elements.length - 1; i >= 0; i = i - 1) {
        if (theasForm.elements[i].name) {
            switch (theasForm.elements[i].nodeName) {
                case 'INPUT':
                    switch (theasForm.elements[i].type) {
                        case 'text':
                        case 'hidden':
                        case 'password':
                        case 'button':
                        case 'reset':
                        case 'submit':
                            theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                            break;
                        case 'checkbox':
                        case 'radio':
                            if (theasForm.elements[i].checked) {
                                theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                            }
                            break;
                        case 'file':
                            break;
                    }
                    break;
                case 'TEXTAREA':
                    theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                    break;
                case 'SELECT':
                    switch (theasForm.elements[i].type) {
                        case 'select-one':
                            theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                            break;
                        case 'select-multiple':
                            for (j = theasForm.elements[i].options.length - 1; j >= 0; j = j - 1) {
                                if (theasForm.elements[i].options[j].selected) {
                                    theasFormData[theasForm.elements[i].name] = theasForm.elements[i].options[j].value;
                                    //to do--not quite right.  Need support for multiple select.
                                }
                            }
                            break;
                    }
                    break;
                case 'BUTTON':
                    switch (theasForm.elements[i].type) {
                        case 'reset':
                        case 'submit':
                        case 'button':
                            theasFormData[theasForm.elements[i].name] = theasForm.elements[i].value;
                            break;
                    }
                    break;
            }

        }
    }

    if (vueObj.theasParams) {
        for (let k in vueObj.theasParams) {
            if (vueObj.theasParams.hasOwnProperty(k)) {
                theasFormData['theas:' + k.replace('$', ':')] = vueObj.theasParams[k];
            }
        }
    }


    // add in data from config.data if applicable
    if (config && config.data) {
        for (let n2 in config.data) {
            if (config.data.hasOwnProperty(n2)) {
                theasFormData[n2] = config.data[n2];
            }
        }
    }


    if (config && config.asyncCmd) {
        theasFormData['cmd'] = config.asyncCmd;
    }


    let buf = null;

    if (config.binaryFiles && config.binaryFiles.length > 0) {

        // we must use FormData
        buf = new FormData();

        for (let n3 in theasFormData) {
            if (theasFormData.hasOwnProperty(n3)) {
                buf.append(n3, theasFormData[n3]);
            }
        }

        for (i = 0; i < config.binaryFiles.length; i = i + 1) {
            let thisFile = config.binaryFiles[i];
            buf.append(thisFile.fieldName, thisFile.binaryData[0], thisFile.fileName)
        }

    }
    else {

        // we can use a string
        buf = '';

        if (theasFormData) {
            for (let n in theasFormData) {
                if (theasFormData.hasOwnProperty(n)) {
                    buf = buf + n + '=' + encodeURIComponent(theasFormData[n]) + '&';
                }
            }
        }
    }


    let axiosConfig = {
        method: 'post',
        url: config.url,
        data: buf,

        onUploadProgress: config.onUploadProgress
    };

    if (config && config.binaryFiles && config.binaryFiles.length > 0) {
        axiosConfig.headers = {'Content-Type': 'multipart/form-data'};
    }

    axios(axiosConfig)
        .then(function (response) {
            //handle success
            console.log(response);

            let params;
            let nameValue;
            let thisName;
            let thisValue;

            let goToURL = '';

            if (response != undefined) {
                params = response.data.split('&');
                for (let i = 0; i < params.length; i++) {
                    nameValue = params[i].split('=');

                    thisName = nameValue[0];
                    thisValue = nameValue[1];

                    if (thisName == 'theas:th:NextPage') {
                        // navigate to specified page
                        goToURL = thisValue;
                    }
                }

                if (!goToURL && config.onSuccessURL) {
                    goToURL = config.onSuccessURL
                }

                if (goToURL) {
                    window.location = goToURL;
                }
            }

        })
        .catch(function (response) {
            //handle error
            console.log(response);
        });

    vueObj.submitted = true;

};

Theas.prototype.clearError = function (doFetchData) {
    // save reference to Theas object
    let thatTheas = this;

    thatTheas.sendAsync({
                    url: 'async',
                    asyncCmd: 'clearError',

                    onResponse: function (rd, response) {
                        thatTheas.thatVue.theasParams.th$ErrorMessage = '';

                        if (typeof thatTheas.thatVue.fetchData == 'function' && doFetchData) {
                            // try to immediately do a fetch
                            thatTheas.thatVue.fetchData();
                        }

                    }

    });

};

Theas.prototype.getCurrentLocation = function () {
    // save reference to Theas object
    let thatTheas = this;

    if (thatTheas.useCurrentLocation) {

        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                function (position) {
                    if (position) {
                        thatTheas.currentLocation = {};
                        thatTheas.currentLocation['lat'] = position.coords.latitude;
                        thatTheas.currentLocation['long'] = position.coords.longitude;
                    }
                },
                function () {
                    thatTheas.currentLocation = null
                });
        }
        else {
            thatTheas.currentLocation = null;
        }
    }
};



Theas.prototype.showModal =  function(msg, title, onClose, goBackOnClose) {
    let $thMsgDlg = this.getModal(msg, title);

    $thMsgDlg.data('origfocused', document.activeElement);


    // define an onClose handler
    $thMsgDlg.on('hidden.bs.modal', function (){
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
            else {
                $origfocused = $($thMsgDlg.data('origfocused'));
                $origfocused.focus();
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

    $thMsgDlg.modal(show=true);

};


Theas.prototype.haveError = function(showModal, backOnError, onClose) {
    // save reference to Theas object
    let thatTheas = this;

    if (typeof showModal == 'undefined') {
        //set default value
        showModal = true;
    }

    if (typeof backOnError == 'undefined') {
        //set default value
        backOnError = false;
    }

    let haveError = false;

    if (this.get('th:ErrorMessage')) {
        let thErrorMsg = this.get('th:ErrorMessage').val();
        if (thErrorMsg) {
            haveError = true;
            th.lastError = thErrorMsg;

            // clear the error message
            this.get('th:ErrorMessage', '');
            this.sendAsync('clearError');

            if (showModal) {
                let msgParts = thErrorMsg.split('|');
                let msgTitle = 'Error';
                if (msgParts.length > 1) {
                  msgTitle = msgParts[1];
                  thErrorMsg = msgParts[0];
                }
                thatTheas.showModal(thErrorMsg, msgTitle, onClose, backOnError);
             }

        }
    }
    return haveError;
};
